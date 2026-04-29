"""Outreach tracker — CSV-backed single source of truth.

All reads and writes to outreach/data/tracker.csv go through this module.
No subagent or pipeline stage manipulates the CSV directly.

Schema matches outreach/CLAUDE.md §2.  State machine matches §4.
"""

from __future__ import annotations

import csv
import fcntl
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

# Column order in tracker.csv (outreach/CLAUDE.md §2)
COLUMNS = [
    "id",
    "company",
    "role_url",
    "role_title",
    "role_tier",
    "person_name",
    "person_title",
    "person_linkedin",
    "person_country",
    "relationship_type",
    "email",
    "email_confidence",
    "linkedin_only",
    "hook",
    "subject",
    "body_path",
    "status",
    "assigned_inbox",
    "send_at_utc",
    "sent_at_utc",
    "replied",
    "notes",
    "last_updated",
]

# Legal state transitions (outreach/CLAUDE.md §4).
# Key = current status, value = set of statuses it can move to.
# "closed" is reachable from any state (Varun manual), handled separately.
LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "research_done": {"people_found", "closed"},
    "people_found": {"contact_found", "closed"},
    "contact_found": {"drafted", "closed"},
    "drafted": {"queued", "closed"},
    "queued": {"sent", "closed"},
    "sent": {"replied", "closed"},
    "replied": {"closed"},
    "closed": set(),
}

_DEFAULT_PATH = Path("outreach/data/tracker.csv")


class StateMachineError(ValueError):
    """Raised when a status transition violates the state machine."""


@dataclass
class Row:
    """One row in outreach/data/tracker.csv.

    Fields default to empty string so callers only need to set the columns
    their pipeline stage is responsible for.
    """

    id: str = ""
    company: str = ""
    role_url: str = ""
    role_title: str = ""
    role_tier: str = ""
    person_name: str = ""
    person_title: str = ""
    person_linkedin: str = ""
    person_country: str = ""
    relationship_type: str = ""
    email: str = ""
    email_confidence: str = ""
    linkedin_only: str = ""
    hook: str = ""
    subject: str = ""
    body_path: str = ""
    status: str = ""
    assigned_inbox: str = ""
    send_at_utc: str = ""
    sent_at_utc: str = ""
    replied: str = ""
    notes: str = ""
    last_updated: str = ""


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _today_utc() -> str:
    """Return today's UTC date as YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _row_from_dict(d: dict[str, str]) -> Row:
    """Build a Row from a CSV dict, ignoring unknown keys."""
    field_names = {f.name for f in fields(Row)}
    return Row(**{k: v for k, v in d.items() if k in field_names})


def init_tracker(path: Path | str = _DEFAULT_PATH) -> Path:
    """Create the tracker CSV with header row if it doesn't exist.

    Args:
        path: Path to the tracker.csv file.

    Returns:
        Resolved Path object.
    """
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(COLUMNS)
    return path


def _read_all_raw(path: Path) -> list[dict[str, str]]:
    """Read tracker CSV into a list of dicts. Returns empty list if missing."""
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _write_all(rows: list[dict[str, str]], path: Path) -> None:
    """Rewrite the entire tracker CSV with file locking."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def read_all(path: Path | str = _DEFAULT_PATH) -> list[Row]:
    """Read all rows from the tracker.

    Args:
        path: Path to tracker.csv.

    Returns:
        List of Row objects. Empty list if file doesn't exist.
    """
    return [_row_from_dict(d) for d in _read_all_raw(Path(path))]


def read_by_status(status: str, path: Path | str = _DEFAULT_PATH) -> list[Row]:
    """Read rows matching a specific status.

    Args:
        status: Status value to filter by (e.g. "drafted", "queued").
        path: Path to tracker.csv.

    Returns:
        List of matching Row objects.
    """
    return [r for r in read_all(path) if r.status == status]


def dedupe_check(
    company: str, person_name: str, path: Path | str = _DEFAULT_PATH
) -> Row | None:
    """Find an existing row matching (company, person_name), case-insensitive.

    Args:
        company: Company name to match.
        person_name: Person name to match.
        path: Path to tracker.csv.

    Returns:
        The matching Row, or None if no match.
    """
    company_lower = company.strip().lower()
    person_lower = person_name.strip().lower()
    for row in read_all(path):
        if (
            row.company.strip().lower() == company_lower
            and row.person_name.strip().lower() == person_lower
        ):
            return row
    return None


def upsert(row: Row, path: Path | str = _DEFAULT_PATH) -> None:
    """Insert or update a row, deduplicating by (company, person_name).

    If a row with the same (company, person_name) exists (case-insensitive),
    update it in-place with all non-empty fields from the new row.  Otherwise
    append.  Assigns a UUID if ``row.id`` is empty.  Always updates
    ``last_updated``.

    Args:
        row: The Row to upsert.
        path: Path to tracker.csv.
    """
    path = Path(path)
    init_tracker(path)

    if not row.id:
        row.id = str(uuid.uuid4())
    row.last_updated = _now_iso()

    raw_rows = _read_all_raw(path)
    company_lower = row.company.strip().lower()
    person_lower = row.person_name.strip().lower()

    matched = False
    new_data = asdict(row)

    for i, existing in enumerate(raw_rows):
        if (
            existing.get("company", "").strip().lower() == company_lower
            and existing.get("person_name", "").strip().lower() == person_lower
        ):
            # Update in-place: keep existing values where new row is empty
            for key in COLUMNS:
                new_val = new_data.get(key, "")
                if new_val:
                    existing[key] = new_val
            # Always stamp last_updated
            existing["last_updated"] = row.last_updated
            raw_rows[i] = existing
            matched = True
            break

    if not matched:
        raw_rows.append(new_data)

    _write_all(raw_rows, path)


def update_status(
    row_id: str, new_status: str, path: Path | str = _DEFAULT_PATH
) -> None:
    """Transition a row to a new status, enforcing the state machine.

    Legal transitions are defined in LEGAL_TRANSITIONS (outreach/CLAUDE.md §4).
    ``closed`` is reachable from any non-closed state.

    Args:
        row_id: UUID of the row to update.
        new_status: Target status.
        path: Path to tracker.csv.

    Raises:
        StateMachineError: If the transition is illegal.
        KeyError: If row_id is not found.
    """
    path = Path(path)
    raw_rows = _read_all_raw(path)

    for i, existing in enumerate(raw_rows):
        if existing.get("id") == row_id:
            current = existing.get("status", "")
            allowed = LEGAL_TRANSITIONS.get(current, set())
            if new_status not in allowed:
                raise StateMachineError(
                    f"Illegal transition: {current!r} -> {new_status!r}"
                )
            existing["status"] = new_status
            existing["last_updated"] = _now_iso()
            raw_rows[i] = existing
            _write_all(raw_rows, path)
            return

    raise KeyError(f"Row not found: {row_id}")


def mark_sent(
    row_id: str, inbox: str, path: Path | str = _DEFAULT_PATH
) -> None:
    """Transition a row to ``sent`` and record send metadata.

    Calls ``update_status`` for state-machine validation, then sets
    ``sent_at_utc`` and ``assigned_inbox``.

    Args:
        row_id: UUID of the row.
        inbox: Gmail address that sent the message.
        path: Path to tracker.csv.

    Raises:
        StateMachineError: If the row isn't in ``queued`` status.
        KeyError: If row_id is not found.
    """
    path = Path(path)
    # Validate transition first
    update_status(row_id, "sent", path)

    # Now set the send metadata
    raw_rows = _read_all_raw(path)
    for i, existing in enumerate(raw_rows):
        if existing.get("id") == row_id:
            existing["sent_at_utc"] = _now_iso()
            existing["assigned_inbox"] = inbox
            existing["last_updated"] = _now_iso()
            raw_rows[i] = existing
            _write_all(raw_rows, path)
            return


def count_sent_today(path: Path | str = _DEFAULT_PATH) -> int:
    """Count messages sent today (UTC date boundary).

    Args:
        path: Path to tracker.csv.

    Returns:
        Number of rows with status ``sent`` and ``sent_at_utc`` on today's
        UTC date.
    """
    today = _today_utc()
    count = 0
    for row in read_all(path):
        if row.status == "sent" and row.sent_at_utc.startswith(today):
            count += 1
    return count


def count_sent_today_by_inbox(
    inbox: str, path: Path | str = _DEFAULT_PATH
) -> int:
    """Count messages sent today from a specific inbox.

    Args:
        inbox: Gmail address to filter by.
        path: Path to tracker.csv.

    Returns:
        Number of rows with status ``sent``, matching inbox, and
        ``sent_at_utc`` on today's UTC date.
    """
    today = _today_utc()
    count = 0
    for row in read_all(path):
        if (
            row.status == "sent"
            and row.assigned_inbox == inbox
            and row.sent_at_utc.startswith(today)
        ):
            count += 1
    return count
