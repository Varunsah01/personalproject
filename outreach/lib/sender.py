"""Outreach sender — picks queued rows, schedules, sends via Gmail API.

Runs as a CLI::

    python -m outreach.lib.sender --tick
    python -m outreach.lib.sender --dry-run

Respects all caps, windows, and stop-file rules from GUARDRAILS.md §1.7
and guidelines.md §3.8.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

# When run directly (python outreach/lib/sender.py), fix sys.path
# before the outreach.lib imports execute.
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from googleapiclient.errors import HttpError  # noqa: E402
from outreach.lib import tracker  # noqa: E402
from outreach.lib.gmail_pool import InboxPool, NoInboxAvailableError, InboxConfig  # noqa: E402
from outreach.lib.timing import next_send_window_utc  # noqa: E402

logger = logging.getLogger(__name__)

# Addresses we must never send to (GUARDRAILS §1.7)
_BANNED_PREFIXES = ("info@", "hello@", "careers@", "support@", "contact@")

_DEFAULT_STOP_PATH = Path("outreach/STOP")
_DEFAULT_TRACKER_PATH = Path("outreach/data/tracker.csv")
_DEFAULT_ENV_PATH = Path(".env.outreach")
_GLOBAL_DAILY_CAP = 25


_HARD_BOUNCE_SIGNALS = (
    "550",
    "5.1.1",
    "5.2.1",
    "invalid recipient",
    "does not exist",
    "no such user",
    "user not found",
    "mailbox unavailable",
)


def _is_generic_alias(email: str) -> bool:
    """Check if an email is a banned generic alias."""
    return any(email.lower().startswith(p) for p in _BANNED_PREFIXES)


def _is_hard_bounce(e: HttpError) -> bool:
    """Return True if the HttpError signals a permanent recipient-side delivery failure.

    The Gmail API embeds SMTP rejection codes in the error body when the receiving
    MTA synchronously rejects the message.  Most NDR bounces arrive later as
    emails to the From inbox and are not detectable here.

    Args:
        e: HttpError raised by the Gmail API.

    Returns:
        True if the error body contains a known hard-bounce signal.
    """
    if e.resp.status != 400:
        return False
    body = e.content.decode("utf-8", errors="ignore").lower()
    return any(signal in body for signal in _HARD_BOUNCE_SIGNALS)


def _is_auth_error(e: HttpError) -> bool:
    """Return True if the HttpError is an authentication or authorisation failure.

    Args:
        e: HttpError raised by the Gmail API.

    Returns:
        True for HTTP 401 or when the body contains known OAuth error tokens.
    """
    if e.resp.status == 401:
        return True
    body = e.content.decode("utf-8", errors="ignore").lower()
    return any(
        s in body for s in ("invalid_grant", "invalid_token", "token has been expired")
    )


def _load_credentials(token_path: Path):
    """Load OAuth credentials from a saved token file.

    Args:
        token_path: Path to the JSON token file created by setup_gmail_oauth.py.

    Returns:
        google.oauth2.credentials.Credentials instance.
    """
    from google.oauth2.credentials import Credentials

    with open(token_path) as f:
        token_data = json.load(f)
    return Credentials.from_authorized_user_info(token_data)


def _archive_eml(
    row_id: str, subject: str, from_addr: str, to_addr: str, body: str
) -> Path:
    """Save a plain-text .eml copy to outreach/data/sent/{date}/{id}.eml.

    Args:
        row_id: UUID of the tracker row.
        subject: Email subject.
        from_addr: Sender address.
        to_addr: Recipient address.
        body: Plain-text body.

    Returns:
        Path to the saved .eml file.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    archive_dir = Path("outreach/data/sent") / today
    archive_dir.mkdir(parents=True, exist_ok=True)

    eml_path = archive_dir / f"{row_id}.eml"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    eml_path.write_text(msg.as_string(), encoding="utf-8")
    return eml_path


def send_one(row: tracker.Row, inbox: InboxConfig) -> bool:
    """Send one outreach email via Gmail API.

    Reads the draft from row.body_path, builds the message, sends it,
    and archives a .eml copy.

    Args:
        row: Tracker row with status ``queued`` and populated email/subject/body_path.
        inbox: InboxConfig for the sending Gmail account.

    Returns:
        True on success, False on error.
    """
    # Guard: never send to generic aliases (GUARDRAILS §1.7)
    if _is_generic_alias(row.email):
        logger.error("Blocked: generic alias %s (row %s)", row.email, row.id)
        return False

    # Guard: email must be present
    if not row.email.strip():
        logger.error("No email address for row %s", row.id)
        return False

    # Read draft body
    draft_path = Path(row.body_path)
    if not draft_path.exists():
        logger.error("Draft not found: %s (row %s)", draft_path, row.id)
        return False
    body = draft_path.read_text(encoding="utf-8")

    try:
        from googleapiclient.discovery import build

        creds = _load_credentials(inbox.oauth_token_path)
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = row.subject
        msg["From"] = inbox.address
        msg["To"] = row.email

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()

        _archive_eml(row.id, row.subject, inbox.address, row.email, body)
        logger.info("Sent to %s (%s) via %s", row.email, row.company, inbox.address)
        return True

    except HttpError as e:
        body_preview = e.content[:200].decode("utf-8", errors="ignore")
        if _is_hard_bounce(e):
            logger.error(
                "permanent_failure: hard_bounce row %s to %s — %s",
                row.id, row.email, body_preview,
            )
            tracker.add_to_suppression(
                email=row.email,
                linkedin_url=row.person_linkedin,
                reason=f"hard_bounce HTTP{e.resp.status}",
            )
        elif _is_auth_error(e):
            logger.error(
                "auth_failed for inbox %s (row %s): HTTP %s — %s",
                inbox.address, row.id, e.resp.status, body_preview,
            )
        elif e.resp.status == 429 or e.resp.status >= 500:
            logger.warning(
                "transient: will retry next tick (HTTP %s) row %s",
                e.resp.status, row.id,
            )
        else:
            logger.error(
                "permanent_failure: HTTP %s row %s — %s",
                e.resp.status, row.id, body_preview,
            )
        return False

    except Exception:
        logger.exception("Failed to send row %s to %s", row.id, row.email)
        return False


def tick(
    *,
    dry_run: bool = False,
    tracker_path: Path = _DEFAULT_TRACKER_PATH,
    env_path: Path = _DEFAULT_ENV_PATH,
    stop_path: Path = _DEFAULT_STOP_PATH,
) -> None:
    """Run one sender tick: process queued rows within the 30-min window.

    For each queued row:
    1. Compute send_at_utc if not already set.
    2. If send_at_utc is within the past 30 min → pick inbox and send.
    3. Otherwise → write send_at_utc to tracker and move on.

    Args:
        dry_run: If True, compute schedules but don't actually send.
        tracker_path: Path to tracker.csv.
        env_path: Path to .env.outreach.
        stop_path: Path to the STOP file.
    """
    # STOP-file check
    if stop_path.exists():
        logger.warning("STOP file present at %s — exiting without sending", stop_path)
        return

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=30)

    # Global cap check
    global_sent = tracker.count_sent_today(tracker_path)
    if global_sent >= _GLOBAL_DAILY_CAP:
        logger.info("Global daily cap reached (%d/%d) — skipping tick",
                     global_sent, _GLOBAL_DAILY_CAP)
        return

    pool = InboxPool.load_from_env(env_path)
    queued_rows = tracker.read_by_status("queued", tracker_path)

    sent_count = 0
    scheduled_count = 0
    skipped_count = 0

    for row in queued_rows:
        # Re-check STOP between each row
        if stop_path.exists():
            logger.warning("STOP file appeared mid-tick — halting")
            break

        # Re-check global cap
        if (global_sent + sent_count) >= _GLOBAL_DAILY_CAP:
            logger.info("Global cap reached mid-tick — halting")
            break

        # Compute or reuse send_at_utc
        if row.send_at_utc:
            send_at = datetime.fromisoformat(row.send_at_utc)
        else:
            send_at = next_send_window_utc(row.person_country or "India", now)
            # Write the scheduled time back to tracker
            row.send_at_utc = send_at.isoformat()
            tracker.upsert(row, tracker_path)
            scheduled_count += 1

        # Is this row ready to send now?
        if not (window_start <= send_at <= now):
            continue

        # Cooldown guard — GUARDRAILS §1.7: never re-send within 14 days
        if row.email or row.person_linkedin:
            if tracker.is_in_cooldown(row.email, row.person_linkedin, path=tracker_path):
                logger.warning(
                    "Cooldown active for row %s (%s) — skipping", row.id, row.email
                )
                skipped_count += 1
                continue

        # Suppression guard
        if row.email or row.person_linkedin:
            if tracker.is_suppressed(row.email, row.person_linkedin):
                logger.warning(
                    "Suppressed: row %s (%s) — skipping", row.id, row.email
                )
                skipped_count += 1
                continue

        if dry_run:
            logger.info("[DRY RUN] Would send row %s to %s at %s",
                        row.id, row.email, send_at.isoformat())
            sent_count += 1
            continue

        # Pick inbox
        try:
            inbox = pool.pick_inbox(tracker_path)
        except NoInboxAvailableError as e:
            logger.warning("No inbox available: %s — halting tick", e)
            break

        # Send
        if send_one(row, inbox):
            tracker.mark_sent(row.id, inbox.address, tracker_path)
            sent_count += 1
        else:
            skipped_count += 1

    prefix = "[DRY RUN] " if dry_run else ""
    logger.info(
        "%sTick complete: %d sent, %d scheduled, %d skipped, %d queued remaining",
        prefix,
        sent_count,
        scheduled_count,
        skipped_count,
        len(queued_rows) - sent_count - skipped_count,
    )


def main() -> None:
    """CLI entry point for the sender."""
    parser = argparse.ArgumentParser(
        description="Outreach sender — process queued rows and send via Gmail API"
    )
    parser.add_argument(
        "--tick", action="store_true",
        help="Run one sender tick (process rows in the current 30-min window)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute schedules and log what would be sent, but don't actually send"
    )
    parser.add_argument(
        "--tracker-path", type=Path, default=_DEFAULT_TRACKER_PATH,
        help="Path to tracker.csv"
    )
    parser.add_argument(
        "--env-path", type=Path, default=_DEFAULT_ENV_PATH,
        help="Path to .env.outreach"
    )
    parser.add_argument(
        "--stop-path", type=Path, default=_DEFAULT_STOP_PATH,
        help="Path to STOP file"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.tick or args.dry_run:
        tick(
            dry_run=args.dry_run,
            tracker_path=args.tracker_path,
            env_path=args.env_path,
            stop_path=args.stop_path,
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
