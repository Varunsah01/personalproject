"""Outreach daily digest — 8 PM IST email summary of the outreach funnel.

Builds and sends a plain-text email covering: funnel snapshot, sent today,
awaiting review, replies, and errors/warnings.

CLI::

    python -m outreach.lib.digest --send
    python -m outreach.lib.digest --print-only
"""

from __future__ import annotations

import argparse
import logging
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

# When run directly, fix sys.path before outreach/core imports.
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from core.notifier import SMTPConfig, build_smtp_config  # noqa: E402
from outreach.lib import tracker  # noqa: E402

logger = logging.getLogger(__name__)

_DEFAULT_TRACKER_PATH = Path("outreach/data/tracker.csv")
_ERROR_KEYWORDS = ("permanent_failure", "auth_failed", "hard_bounce")

# Pipeline status order for the funnel table.
_STATUS_ORDER = [
    "research_done",
    "people_found",
    "contact_found",
    "drafted",
    "queued",
    "sent",
    "replied",
    "closed",
]


# ---------------------------------------------------------------------------
# Subject
# ---------------------------------------------------------------------------

def build_subject(date: str, counts: dict[str, int]) -> str:
    """Build a one-line email subject summarising the funnel.

    Args:
        date: ISO date string, e.g. "2026-04-29".
        counts: Dict from tracker.funnel_counts().

    Returns:
        Subject line, e.g. "Outreach digest 2026-04-29 — 7 sent, 5 queued, 12 drafted"
    """
    sent = counts.get("sent", 0)
    queued = counts.get("queued", 0)
    drafted = counts.get("drafted", 0)
    return f"Outreach digest {date} \u2014 {sent} sent, {queued} queued, {drafted} drafted"


# ---------------------------------------------------------------------------
# Body
# ---------------------------------------------------------------------------

def build_body(date: str, tracker_path: Path = _DEFAULT_TRACKER_PATH) -> str:
    """Build the five-section plain-text digest body.

    Args:
        date: ISO date string for the digest (used to filter "today" rows).
        tracker_path: Path to tracker.csv.

    Returns:
        Complete plain-text email body.
    """
    all_rows = tracker.read_all(tracker_path)
    counts = tracker.funnel_counts(tracker_path)
    lines: list[str] = []

    sep = "=" * 48

    lines += [
        f"Outreach daily digest \u2014 {date}",
        sep,
        "",
    ]

    # ── 1. Funnel snapshot ────────────────────────────────────────────
    lines += [
        "FUNNEL SNAPSHOT",
        "---------------",
    ]
    for status in _STATUS_ORDER:
        count = counts.get(status, 0)
        lines.append(f"  {status:<18} {count}")
    lines.append("")

    # ── 2. Sent today ────────────────────────────────────────────────
    lines += [
        "SENT TODAY",
        "----------",
    ]
    sent_today = [
        r for r in all_rows
        if r.status == "sent" and r.sent_at_utc.startswith(date)
    ]
    sent_today.sort(key=lambda r: r.sent_at_utc)

    if sent_today:
        lines.append(f"  {'Time':<8} {'Company':<20} {'Person':<18} {'Role':<22} {'Inbox'}")
        lines.append("  " + "-" * 78)
        for r in sent_today:
            time_str = r.sent_at_utc[11:16] if len(r.sent_at_utc) > 16 else "??:??"
            company = (r.company or "")[:20]
            person = (r.person_name or "")[:18]
            role = (r.role_title or "")[:22]
            inbox = r.assigned_inbox or ""
            lines.append(f"  {time_str:<8} {company:<20} {person:<18} {role:<22} {inbox}")
    else:
        lines.append("  No messages sent today.")
    lines.append("")

    # ── 3. Awaiting review ───────────────────────────────────────────
    drafted = tracker.read_by_status("drafted", tracker_path)
    drafted.sort(key=lambda r: r.last_updated or "")
    preview = drafted[:10]

    lines += [
        f"AWAITING REVIEW ({len(drafted)} drafted)",
        "-" * 48,
    ]
    if preview:
        for r in preview:
            short_id = r.id[:8] if r.id else "?"
            company = r.company or "(unknown)"
            person = r.person_name or "(unknown)"
            lines.append(f"  {short_id}  {company} \u2014 {person}")
        if len(drafted) > 10:
            lines.append(f"  ... and {len(drafted) - 10} more.")
    else:
        lines.append("  No drafts awaiting review.")
    lines.append("")
    lines.append("  Run: python outreach/review.py --status drafted")
    lines.append("")

    # ── 4. Replies received today ────────────────────────────────────
    lines += [
        "REPLIES TODAY",
        "-------------",
    ]
    replies_today = [
        r for r in all_rows
        if r.status == "replied" and r.last_updated.startswith(date)
    ]
    if replies_today:
        for r in replies_today:
            company = r.company or "(unknown)"
            person = r.person_name or "(unknown)"
            role = r.role_title or "(unknown)"
            lines.append(f"  {company} \u2014 {person} ({role})")
    else:
        lines.append("  No replies today.")
    lines.append("")

    # ── 5. Errors / warnings ────────────────────────────────────────
    lines += [
        "ERRORS / WARNINGS",
        "-----------------",
    ]
    error_rows = [
        r for r in all_rows
        if r.notes and any(kw in r.notes.lower() for kw in _ERROR_KEYWORDS)
    ]
    error_rows.sort(key=lambda r: r.last_updated or "", reverse=True)
    error_rows = error_rows[:5]

    if error_rows:
        for r in error_rows:
            company = r.company or "(unknown)"
            person = r.person_name or "(unknown)"
            notes = (r.notes or "")[:80]
            lines.append(f"  {company} \u2014 {person}")
            lines.append(f"    {notes}")
    else:
        lines.append("  No errors or warnings.")
    lines.append("")

    lines.append("-" * 48)
    lines.append("Generated by outreach digest.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

def send_digest(
    date: str,
    tracker_path: Path = _DEFAULT_TRACKER_PATH,
    smtp_config: SMTPConfig | None = None,
    print_only: bool = False,
) -> None:
    """Build and send (or print) the outreach daily digest.

    Args:
        date: ISO date string, e.g. "2026-04-29".
        tracker_path: Path to tracker.csv.
        smtp_config: SMTP settings from build_smtp_config(). If None,
            logs a warning and returns (unless print_only).
        print_only: If True, print subject + body to stdout; don't send.
    """
    if smtp_config is None:
        if not print_only:
            logger.warning("No SMTP config \u2014 digest not sent (set SMTP_* vars in .env)")
            return
        smtp_config = SMTPConfig(
            host="", port=587, user="<not configured>", password="", digest_to="<not configured>"
        )

    counts = tracker.funnel_counts(tracker_path)
    subject = build_subject(date, counts)
    body = build_body(date, tracker_path)

    if print_only:
        print(f"Subject: {subject}")
        print(f"To:      {smtp_config.digest_to}")
        print(f"From:    {smtp_config.user}")
        print()
        print(body)
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_config.user
    msg["To"] = smtp_config.digest_to

    try:
        with smtplib.SMTP(smtp_config.host, smtp_config.port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_config.user, smtp_config.password)
            server.sendmail(smtp_config.user, [smtp_config.digest_to], msg.as_string())
        logger.info("Outreach digest sent to %s", smtp_config.digest_to)
    except smtplib.SMTPAuthenticationError as exc:
        logger.error(
            "SMTP authentication failed \u2014 check SMTP_USER / SMTP_PASSWORD: %s", exc
        )
    except smtplib.SMTPConnectError as exc:
        logger.error(
            "SMTP connect failed (%s:%d): %s", smtp_config.host, smtp_config.port, exc
        )
    except smtplib.SMTPException as exc:
        logger.error("SMTP error sending outreach digest: %s", exc)
    except OSError as exc:
        logger.error("Network error sending outreach digest: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point for the outreach digest."""
    parser = argparse.ArgumentParser(
        description="Outreach daily digest \u2014 email summary of the outreach funnel"
    )
    parser.add_argument(
        "--send", action="store_true",
        help="Send digest email via SMTP"
    )
    parser.add_argument(
        "--print-only", action="store_true",
        help="Print subject + body to stdout (no email sent)"
    )
    parser.add_argument(
        "--tracker-path", type=Path, default=_DEFAULT_TRACKER_PATH,
        help="Path to tracker.csv"
    )
    parser.add_argument(
        "--date", default=None,
        help="Override date (YYYY-MM-DD); defaults to today UTC"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not args.send and not args.print_only:
        parser.print_help()
        sys.exit(1)

    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    smtp_config = build_smtp_config() if args.send else None

    send_digest(
        date=date,
        tracker_path=args.tracker_path,
        smtp_config=smtp_config,
        print_only=args.print_only,
    )


if __name__ == "__main__":
    main()
