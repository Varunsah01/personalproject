#!/usr/bin/env python3
"""Write-back helper — bridges dashboard approve/reject to tracker.csv.

Called by the Next.js dashboard API via subprocess:

    python3 outreach/lib/writeback.py --row-id <uuid> --action approve
    python3 outreach/lib/writeback.py --row-id <uuid> --action reject --reason "wrong-tone" --notes "Hook is off"

This is the ONLY path from dashboard writes to tracker.csv.
Reuses tracker.py for file locking, state machine validation, and cooldown checks.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from outreach.lib import tracker  # noqa: E402

TRACKER_PATH = _PROJECT_ROOT / "outreach" / "data" / "tracker.csv"


def approve(row_id: str) -> None:
    """Promote a drafted row to queued in tracker.csv."""
    tracker.promote_to_queued(row_id, TRACKER_PATH)


def reject(row_id: str, reason: str, notes: str) -> None:
    """Close a drafted row with rejection reason."""
    combined = f"rejected: {reason}"
    if notes:
        combined += f" — {notes}"
    tracker.update_notes(row_id, combined, TRACKER_PATH)
    tracker.update_status(row_id, "closed", TRACKER_PATH)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dashboard write-back to tracker.csv")
    parser.add_argument("--row-id", required=True, help="UUID of the tracker row")
    parser.add_argument(
        "--action", choices=["approve", "reject"], required=True
    )
    parser.add_argument("--reason", default="", help="Rejection reason chip")
    parser.add_argument("--notes", default="", help="Freeform rejection note")
    args = parser.parse_args()

    if args.action == "approve":
        approve(args.row_id)
        print(f"OK: approved {args.row_id}")
    else:
        reject(args.row_id, args.reason, args.notes)
        print(f"OK: rejected {args.row_id}")


if __name__ == "__main__":
    main()
