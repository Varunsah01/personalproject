"""Auto-close stale outreach rows."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta, timezone

from outreach.lib import tracker

logger = logging.getLogger(__name__)

# Statuses eligible for auto-close (non-terminal, not awaiting send)
STALE_ELIGIBLE = {
    "research_done",
    "people_found",
    "contact_found",
    "drafted",
    "linkedin_queue",
}


def close_stale(
    dry_run: bool = False, path: str | None = None
) -> int:
    """Close rows stuck at non-terminal statuses for >30 days.

    Args:
        dry_run: If True, log what would be closed but don't modify.
        path: Path to tracker.csv.  Defaults to the standard location.

    Returns:
        Number of rows closed (or that would be closed in dry-run mode).
    """
    if path is None:
        path = tracker._DEFAULT_PATH
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    rows = tracker.read_all(path)
    closed = 0

    for row in rows:
        if row.status not in STALE_ELIGIBLE:
            continue
        if not row.last_updated:
            continue
        try:
            updated = datetime.fromisoformat(row.last_updated)
        except ValueError:
            continue
        if updated >= cutoff:
            continue

        prev = row.status
        if dry_run:
            logger.info(
                "Would close %s (%s, stale at %s)", row.id, row.company, prev
            )
        else:
            tracker.update_status(row.id, "closed", path)
            old_notes = row.notes
            new_note = f"auto-closed: stale > 30d at {prev}"
            combined = f"{old_notes}; {new_note}" if old_notes else new_note
            tracker.update_notes(row.id, combined, path)
            logger.info("Closed %s (%s): %s", row.id, row.company, new_note)
        closed += 1

    return closed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Stale row cleanup")
    parser.add_argument("--close-stale", action="store_true", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    n = close_stale(dry_run=args.dry_run)
    print(f"{'Would close' if args.dry_run else 'Closed'} {n} stale row(s)")
