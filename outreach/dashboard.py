"""Outreach dashboard — at-a-glance funnel state on an 80-column terminal.

Read-only display tool. No writes, no side effects.

CLI::

    python outreach/dashboard.py
    python outreach/dashboard.py --by-tier
    python outreach/dashboard.py --needs-review
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from outreach.lib import tracker  # noqa: E402
from outreach.lib.gmail_pool import InboxPool  # noqa: E402

_DEFAULT_TRACKER_PATH = Path("outreach/data/tracker.csv")
_DEFAULT_ENV_PATH = Path(".env.outreach")
_DEFAULT_STOP_PATH = Path("outreach/STOP")

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
# Helpers
# ---------------------------------------------------------------------------

def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _week_ago_utc() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")


def _sent_in_range(rows: list[tracker.Row], start_date: str) -> int:
    """Count sent rows where sent_at_utc >= start_date."""
    return sum(
        1 for r in rows
        if r.status == "sent" and r.sent_at_utc >= start_date
    )


def _replied_in_range(rows: list[tracker.Row], start_date: str) -> int:
    """Count replied rows where last_updated >= start_date."""
    return sum(
        1 for r in rows
        if r.status == "replied" and r.last_updated >= start_date
    )


# ---------------------------------------------------------------------------
# Default dashboard
# ---------------------------------------------------------------------------

def print_dashboard(
    tracker_path: Path = _DEFAULT_TRACKER_PATH,
    env_path: Path = _DEFAULT_ENV_PATH,
    stop_path: Path = _DEFAULT_STOP_PATH,
) -> None:
    """Print the full dashboard to stdout."""
    all_rows = tracker.read_all(tracker_path)
    counts = tracker.funnel_counts(tracker_path)
    today = _today_utc()
    week_ago = _week_ago_utc()

    sent_today = sum(
        1 for r in all_rows
        if r.status == "sent" and r.sent_at_utc.startswith(today)
    )
    sent_7d = _sent_in_range(all_rows, week_ago)
    replied_7d = _replied_in_range(all_rows, week_ago)

    # ── Funnel table ──────────────────────────────────────────────────
    print()
    print("OUTREACH FUNNEL")
    print("\u2500" * 40)
    rule20 = "\u2500" * 20
    rule6 = "\u2500" * 6
    print(f"  {'status':<20} {'count':>6}")
    print(f"  {rule20} {rule6}")
    for status in _STATUS_ORDER:
        c = counts.get(status, 0)
        label = status
        note = ""
        if status == "drafted" and c > 0:
            note = "  \u2190 awaiting review"
        print(f"  {label:<20} {c:>6}{note}")

    print(f"  {rule20} {rule6}")
    print(f"  {'sent (today)':<20} {sent_today:>6}")
    print(f"  {'sent (last 7d)':<20} {sent_7d:>6}")
    print(f"  {'replied (last 7d)':<20} {replied_7d:>6}")
    print()

    # ── Inbox usage ───────────────────────────────────────────────────
    print("INBOX USAGE TODAY")
    print("\u2500" * 40)
    try:
        pool = InboxPool.load_from_env(env_path)
        for inbox in pool.inboxes:
            used = tracker.count_sent_today_by_inbox(inbox.address, tracker_path)
            bar_len = int(used / max(inbox.daily_cap, 1) * 20)
            bar = "\u2588" * bar_len + "\u2591" * (20 - bar_len)
            print(f"  {inbox.address:<30} {used:>2}/{inbox.daily_cap:<2}  {bar}")
        if not pool.inboxes:
            print("  No inboxes configured.")
    except Exception:
        print("  Could not load inbox config (.env.outreach).")
    print()

    # ── Stop file ─────────────────────────────────────────────────────
    stop_present = stop_path.exists()
    label = "YES \u2014 sender paused" if stop_present else "no"
    print(f"STOP FILE: {label}")
    print()

    # ── Last 5 updated rows ──────────────────────────────────────────
    print("LAST 5 UPDATED")
    print("\u2500" * 78)
    sorted_rows = sorted(
        all_rows, key=lambda r: r.last_updated or "", reverse=True
    )[:5]
    if sorted_rows:
        for r in sorted_rows:
            ts = (r.last_updated or "")[:16]
            status = (r.status or "")[:15]
            company = (r.company or "")[:25]
            person = r.person_name or ""
            print(f"  {ts:<17} {status:<15} {company:<25} {person}")
    else:
        print("  No rows in tracker.")
    print()


# ---------------------------------------------------------------------------
# --by-tier
# ---------------------------------------------------------------------------

def print_by_tier(tracker_path: Path = _DEFAULT_TRACKER_PATH) -> None:
    """Print funnel breakdown grouped by role_tier."""
    all_rows = tracker.read_all(tracker_path)

    # Group by tier
    tiers: dict[str, dict[str, int]] = {}
    for r in all_rows:
        tier = r.role_tier or "(none)"
        if tier not in tiers:
            tiers[tier] = {}
        tiers[tier][r.status] = tiers[tier].get(r.status, 0) + 1

    print()
    print("FUNNEL BY TIER")
    print("\u2500" * 60)

    # Header
    tier_keys = sorted(tiers.keys())
    header = f"  {'status':<18}"
    for t in tier_keys:
        header += f" {t:>6}"
    header += f" {'total':>6}"
    print(header)
    rule18 = "\u2500" * 18
    rule6 = "\u2500" * 6
    col_rule = (" " + rule6) * len(tier_keys)
    print(f"  {rule18}{col_rule} {rule6}")

    for status in _STATUS_ORDER:
        line = f"  {status:<18}"
        row_total = 0
        for t in tier_keys:
            c = tiers[t].get(status, 0)
            row_total += c
            line += f" {c:>6}"
        line += f" {row_total:>6}"
        print(line)

    # Totals row
    line = f"  {'TOTAL':<18}"
    grand = 0
    for t in tier_keys:
        t_total = sum(tiers[t].values())
        grand += t_total
        line += f" {t_total:>6}"
    line += f" {grand:>6}"
    print(f"  {rule18}{col_rule} {rule6}")
    print(line)
    print()


# ---------------------------------------------------------------------------
# --needs-review
# ---------------------------------------------------------------------------

def print_needs_review(tracker_path: Path = _DEFAULT_TRACKER_PATH) -> None:
    """List all drafted rows, copy-paste-friendly for review.py."""
    drafted = tracker.read_by_status("drafted", tracker_path)
    drafted.sort(key=lambda r: r.last_updated or "")

    print()
    print(f"DRAFTED ROWS AWAITING REVIEW ({len(drafted)})")
    print("\u2500" * 78)

    if not drafted:
        print("  None.")
        print()
        return

    for r in drafted:
        short_id = r.id[:8] if r.id else "?"
        company = (r.company or "")[:25]
        person = r.person_name or ""
        print(f"  {short_id}  {company:<25} {person}")

    print()
    print("  To review a specific row:")
    print("    python outreach/review.py --row-id <full-uuid>")
    print()
    print("  To review all:")
    print("    python outreach/review.py")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Outreach dashboard \u2014 at-a-glance funnel state"
    )
    parser.add_argument(
        "--by-tier", action="store_true",
        help="Break funnel down by role_tier (T1/T2/T3)"
    )
    parser.add_argument(
        "--needs-review", action="store_true",
        help="List all drafted rows awaiting review"
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

    if args.by_tier:
        print_by_tier(args.tracker_path)
    elif args.needs_review:
        print_needs_review(args.tracker_path)
    else:
        print_dashboard(args.tracker_path, args.env_path, args.stop_path)


if __name__ == "__main__":
    main()
