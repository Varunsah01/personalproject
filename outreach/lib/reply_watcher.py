"""Reply watcher — detects incoming replies to sent outreach emails.

Polls Gmail API for each inbox, matches replies against sent tracker rows
by subject + recipient, and advances status from ``sent`` to ``replied``.

Usage:
    python -m outreach.lib.reply_watcher --tick       # one pass
    python -m outreach.lib.reply_watcher --dry-run    # read-only, log what would change
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from outreach.lib import tracker
from outreach.lib.gmail_pool import InboxConfig, InboxPool

logger = logging.getLogger(__name__)

_DEFAULT_TRACKER_PATH = Path("outreach/data/tracker.csv")
_DEFAULT_ENV_PATH = Path(".env.outreach")
_DEFAULT_STOP_PATH = Path("outreach/STOP")
_LOG_DIR = Path("data/logs")


def _load_credentials(token_path: Path):
    """Load OAuth credentials from a saved token file.

    Reuses the same pattern as sender.py.
    """
    import json

    from google.oauth2.credentials import Credentials

    with open(token_path) as f:
        token_data = json.load(f)
    return Credentials.from_authorized_user_info(token_data)


def _build_service(inbox: InboxConfig):
    """Build a Gmail API service for the given inbox."""
    from googleapiclient.discovery import build

    creds = _load_credentials(inbox.oauth_token_path)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _find_replies(service, sent_rows: list[tracker.Row]) -> list[tracker.Row]:
    """Search Gmail for replies to the given sent rows.

    For each row, queries the inbox for threads matching the subject and
    recipient.  If a thread has messages from someone other than the
    sending inbox (i.e. a reply), the row is included in the result.

    Args:
        service: Authenticated Gmail API service.
        sent_rows: Rows with status ``sent``, all from the same inbox.

    Returns:
        Subset of rows that have received replies.
    """
    replied: list[tracker.Row] = []

    for row in sent_rows:
        if not row.email or not row.subject:
            continue

        # Gmail search: threads sent to this person with this subject
        query = f"to:{row.email} subject:{row.subject}"
        try:
            result = (
                service.users()
                .messages()
                .list(userId="me", q=query, maxResults=5)
                .execute()
            )
        except Exception:
            logger.warning("Gmail search failed for row %s", row.id)
            continue

        messages = result.get("messages", [])
        if not messages:
            continue

        # Get the thread for the first matching message
        thread_id = messages[0].get("threadId")
        if not thread_id:
            continue

        try:
            thread = (
                service.users()
                .threads()
                .get(userId="me", id=thread_id, format="metadata",
                     metadataHeaders=["From", "To"])
                .execute()
            )
        except Exception:
            logger.warning("Thread fetch failed for row %s, thread %s", row.id, thread_id)
            continue

        thread_messages = thread.get("messages", [])
        if len(thread_messages) < 2:
            # Only our outbound message — no reply yet
            continue

        # Check if any message in the thread is FROM the recipient
        for tmsg in thread_messages[1:]:
            headers = {
                h["name"].lower(): h["value"]
                for h in tmsg.get("payload", {}).get("headers", [])
            }
            from_addr = headers.get("from", "").lower()
            if row.email.lower() in from_addr:
                replied.append(row)
                break

    return replied


def tick(
    *,
    dry_run: bool = False,
    tracker_path: Path = _DEFAULT_TRACKER_PATH,
    env_path: Path = _DEFAULT_ENV_PATH,
    stop_path: Path = _DEFAULT_STOP_PATH,
) -> None:
    """Run one reply-watcher pass.

    1. Check STOP file.
    2. Load sent rows where replied != 'true'.
    3. For each inbox, search Gmail for replies.
    4. Update tracker: replied='true', status='replied', append note.
    """
    # STOP-file guard
    if stop_path.exists():
        logger.info("STOP file found at %s — exiting", stop_path)
        return

    # Load sent rows that haven't been marked as replied
    sent_rows = [
        r for r in tracker.read_by_status("sent", tracker_path)
        if r.replied != "true"
    ]
    if not sent_rows:
        logger.info("No unreplied sent rows — nothing to do")
        return

    logger.info("Checking %d sent rows for replies", len(sent_rows))

    # Group rows by assigned_inbox
    inbox_groups: dict[str, list[tracker.Row]] = {}
    for row in sent_rows:
        inbox_addr = row.assigned_inbox
        if not inbox_addr:
            continue
        inbox_groups.setdefault(inbox_addr, []).append(row)

    # Load inbox pool to get credentials
    pool = InboxPool.load_from_env(env_path)
    inbox_map = {cfg.address: cfg for cfg in pool.inboxes}

    replied_count = 0

    for inbox_addr, rows in inbox_groups.items():
        cfg = inbox_map.get(inbox_addr)
        if not cfg:
            logger.warning("No config for inbox %s — skipping %d rows", inbox_addr, len(rows))
            continue

        # Re-check STOP file between inboxes
        if stop_path.exists():
            logger.info("STOP file detected mid-run — halting")
            break

        try:
            service = _build_service(cfg)
        except Exception:
            logger.exception("Failed to auth inbox %s", inbox_addr)
            continue

        replied_rows = _find_replies(service, rows)

        for row in replied_rows:
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            existing_notes = row.notes or ""
            note_suffix = f"replied_at_utc={now_iso}"
            new_notes = f"{existing_notes}; {note_suffix}".lstrip("; ")

            if dry_run:
                logger.info(
                    "[DRY RUN] Would mark replied: %s (%s → %s)",
                    row.id, row.company, row.email,
                )
            else:
                tracker.update_status(row.id, "replied", tracker_path)
                tracker.update_notes(row.id, new_notes, tracker_path)
                # Also set the replied field to 'true'
                _set_replied_flag(row.id, tracker_path)
                logger.info(
                    "Marked replied: %s (%s → %s)",
                    row.id, row.company, row.email,
                )

            replied_count += 1

    logger.info(
        "Reply watcher complete: %d replied%s",
        replied_count,
        " (dry run)" if dry_run else "",
    )


def _set_replied_flag(row_id: str, path: Path) -> None:
    """Set the ``replied`` field to ``true`` on a tracker row."""
    raw_rows = tracker._read_all_raw(path)
    for i, existing in enumerate(raw_rows):
        if existing.get("id") == row_id:
            existing["replied"] = "true"
            existing["last_updated"] = tracker._now_iso()
            raw_rows[i] = existing
            tracker._write_all(raw_rows, path)
            return


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Check for replies to sent outreach emails.",
    )
    parser.add_argument(
        "--tick", action="store_true",
        help="Run one reply-check pass.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Read-only mode — log what would change without updating tracker.",
    )
    parser.add_argument(
        "--tracker-path", type=Path, default=_DEFAULT_TRACKER_PATH,
        help="Path to tracker.csv.",
    )
    parser.add_argument(
        "--env-path", type=Path, default=_DEFAULT_ENV_PATH,
        help="Path to .env.outreach.",
    )
    parser.add_argument(
        "--stop-path", type=Path, default=_DEFAULT_STOP_PATH,
        help="Path to STOP file.",
    )
    args = parser.parse_args()

    # Logging to stdout + file
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = _LOG_DIR / f"reply_watcher-{today}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
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
