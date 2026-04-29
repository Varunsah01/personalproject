"""Outreach draft reviewer — interactive CLI for the drafted-to-queued gate.

Walk through drafted outreach rows, view/edit drafts, and approve or reject
them.  This is the manual human gate required by GUARDRAILS.md section 1.7 and
outreach/CLAUDE.md section 4.

CLI::

    python outreach/review.py
    python outreach/review.py --limit 5
    python outreach/review.py --row-id <uuid>
    python outreach/review.py --status drafted
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

# When run directly, fix sys.path before outreach imports.
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from outreach.lib import tracker  # noqa: E402
from outreach.lib.tracker import StateMachineError  # noqa: E402

logger = logging.getLogger(__name__)

_DEFAULT_TRACKER_PATH = Path("outreach/data/tracker.csv")
_MAX_RECOMMENDED_SENTENCES = 6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_sentences(text: str) -> int:
    """Count sentences using a simple punctuation heuristic."""
    # Split on sentence-ending punctuation followed by whitespace or end-of-string.
    parts = re.split(r"[.!?](?:\s|$)", text.strip())
    # Filter out empty fragments from trailing splits.
    return len([p for p in parts if p.strip()])


def _draft_warnings(body: str) -> list[str]:
    """Return soft-warning strings about the draft body (may be empty list)."""
    warnings: list[str] = []
    if not body.strip():
        warnings.append("Draft is empty")
        return warnings
    n = _count_sentences(body)
    if n > _MAX_RECOMMENDED_SENTENCES:
        warnings.append(
            f"Draft has {n} sentences (recommended <= {_MAX_RECOMMENDED_SENTENCES})"
        )
    return warnings


def _display_row(idx: int, total: int, row: tracker.Row, body: str) -> None:
    """Print formatted row metadata, draft body, and any warnings."""
    sep = "\u2500" * 56
    company = row.company or "(unknown)"
    role = row.role_title or "(unknown)"
    print()
    print(sep)
    print(f"  Row {idx} / {total}   {company} \u2014 {role}")
    print(sep)

    person = row.person_name or "(unknown)"
    title = row.person_title or ""
    person_display = f"{person} ({title})" if title else person
    email_display = row.email or "(none)"
    if row.email_confidence:
        email_display += f" ({row.email_confidence})"

    print(f"  Person:   {person_display}")
    print(f"  Email:    {email_display}")
    print(f"  LinkedIn: {row.person_linkedin or '(none)'}")
    print(f"  Role URL: {row.role_url or '(none)'}")
    print(f"  Subject:  {row.subject or '(none)'}")

    print()
    print("  \u2500\u2500 Draft body \u2500\u2500")
    if body.strip():
        for line in body.splitlines():
            print(f"  {line}")
    else:
        print("  (empty)")
    print("  \u2500" * 16)

    for w in _draft_warnings(body):
        print(f"  WARNING: {w}")


def _display_body(body: str) -> None:
    """Re-display just the body and warnings (after an edit)."""
    print()
    print("  \u2500\u2500 Draft body (updated) \u2500\u2500")
    if body.strip():
        for line in body.splitlines():
            print(f"  {line}")
    else:
        print("  (empty)")
    print("  \u2500" * 16)
    for w in _draft_warnings(body):
        print(f"  WARNING: {w}")


def _prompt_action(input_fn: callable = input) -> str:
    """Show action menu, loop until a valid choice is entered."""
    valid = {"a", "e", "s", "r", "q"}
    print("  [a]pprove  [e]dit  [s]kip  [r]eject  [q]uit")
    while True:
        try:
            choice = input_fn("  Action: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "q"
        if choice in valid:
            return choice
        print(f"  Invalid choice '{choice}' \u2014 enter a, e, s, r, or q.")


def _open_in_editor(path: Path) -> None:
    """Open a file in the user's $EDITOR (default: vi)."""
    editor = os.environ.get("EDITOR", "vi")
    subprocess.call([editor, str(path)])


# ---------------------------------------------------------------------------
# Main review loop
# ---------------------------------------------------------------------------

def review(
    *,
    status: str = "drafted",
    limit: int = 10,
    row_id: str | None = None,
    tracker_path: Path = _DEFAULT_TRACKER_PATH,
    input_fn: callable = input,
) -> dict[str, int]:
    """Interactive review loop for outreach drafts.

    Args:
        status: Which status bucket to review.
        limit: Max rows to process per session.
        row_id: If set, review only this specific row.
        tracker_path: Path to tracker.csv.
        input_fn: Callable for user input (injectable for tests).

    Returns:
        Dict with keys: reviewed, approved, skipped, rejected.
    """
    result = {"reviewed": 0, "approved": 0, "skipped": 0, "rejected": 0}

    # Resolve rows
    if row_id:
        all_rows = tracker.read_all(tracker_path)
        matched = [r for r in all_rows if r.id == row_id]
        if not matched:
            print(f"Row not found: {row_id}")
            return result
        if matched[0].status != status:
            print(
                f"Row {row_id} has status '{matched[0].status}', "
                f"expected '{status}'."
            )
            return result
        rows = matched
    else:
        rows = tracker.read_by_status(status, tracker_path)[:limit]

    if not rows:
        print(f"No rows with status '{status}' found.")
        return result

    print(f"\n{len(rows)} draft(s) to review.\n")

    approved = 0
    skipped = 0
    rejected = 0

    for idx, row in enumerate(rows, 1):
        # Read draft body
        body = ""
        if row.body_path:
            draft_path = Path(row.body_path)
            if draft_path.exists():
                body = draft_path.read_text(encoding="utf-8")
            else:
                print(f"  WARNING: Draft file not found: {row.body_path}")

        _display_row(idx, len(rows), row, body)

        # Inner action loop (may re-prompt after edit or blocked approval)
        while True:
            action = _prompt_action(input_fn)

            if action == "a":
                try:
                    tracker.promote_to_queued(row.id, tracker_path)
                    print(f"  Approved \u2014 row {row.id} is now queued.")
                    approved += 1
                    break
                except StateMachineError as e:
                    print(f"  Cannot approve: {e}")
                    print("  Choose another action.")
                    continue

            elif action == "e":
                if not row.body_path:
                    print("  No body_path set \u2014 cannot edit.")
                    continue
                _open_in_editor(Path(row.body_path))
                # Re-read after editor closes
                draft_path = Path(row.body_path)
                if draft_path.exists():
                    body = draft_path.read_text(encoding="utf-8")
                else:
                    body = ""
                _display_body(body)
                continue

            elif action == "s":
                print("  Skipped.")
                skipped += 1
                break

            elif action == "r":
                try:
                    reason = input_fn("  Rejection reason: ").strip()
                except (EOFError, KeyboardInterrupt):
                    reason = ""
                tracker.update_status(row.id, "closed", tracker_path)
                if reason:
                    tracker.update_notes(row.id, reason, tracker_path)
                print(f"  Rejected \u2014 row {row.id} moved to closed.")
                rejected += 1
                break

            elif action == "q":
                print("\nQuitting \u2014 remaining rows unchanged.")
                result["reviewed"] = approved + skipped + rejected
                result["approved"] = approved
                result["skipped"] = skipped
                result["rejected"] = rejected
                _print_summary(result)
                return result

    result["reviewed"] = approved + skipped + rejected
    result["approved"] = approved
    result["skipped"] = skipped
    result["rejected"] = rejected
    _print_summary(result)
    return result


def _print_summary(result: dict[str, int]) -> None:
    """Print end-of-session summary line."""
    print(
        f"\nReviewed: {result['reviewed']} | "
        f"Approved: {result['approved']} | "
        f"Skipped: {result['skipped']} | "
        f"Rejected: {result['rejected']}"
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point for the draft reviewer."""
    parser = argparse.ArgumentParser(
        description="Review drafted outreach messages \u2014 approve, edit, reject, or skip"
    )
    parser.add_argument(
        "--status", default="drafted",
        help="Which status bucket to review (default: drafted)"
    )
    parser.add_argument(
        "--limit", type=int, default=10,
        help="Max rows per session (default: 10)"
    )
    parser.add_argument(
        "--row-id", dest="row_id", default=None,
        help="Review one specific row by UUID"
    )
    parser.add_argument(
        "--tracker-path", type=Path, default=_DEFAULT_TRACKER_PATH,
        help="Path to tracker.csv"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    review(
        status=args.status,
        limit=args.limit,
        row_id=args.row_id,
        tracker_path=args.tracker_path,
    )


if __name__ == "__main__":
    main()
