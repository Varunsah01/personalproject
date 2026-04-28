"""CSV logger, dedupe engine, and daily-cap counter.

All CSV writes in the project go through this module — no platform writes
its own CSV (CLAUDE.md §7). Schema matches CLAUDE.md §9, behaviour matches
guidelines.md §3.5.
"""

from __future__ import annotations

import csv
import fcntl
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# Column order in applications_log.csv (CLAUDE.md §9)
COLUMNS = [
    "date_applied",
    "time_applied",
    "platform",
    "company_name",
    "role_title",
    "experience_required",
    "location",
    "job_url",
    "fit_score",
    "status",
    "notes",
]

# Query-param prefixes stripped during URL normalisation (guidelines.md §3.1 step 2)
_TRACKING_PREFIXES = ("utm_", "ref", "source", "fbclid", "gclid")


@dataclass
class LogEntry:
    """One row in applications_log.csv."""

    date_applied: str      # ISO date, e.g. "2026-04-28"
    time_applied: str      # HH:MM, e.g. "14:32"
    platform: str          # e.g. "naukri"
    company_name: str
    role_title: str
    experience_required: str
    location: str
    job_url: str
    fit_score: float
    status: str            # applied | applied_unconfirmed | queued | skipped | error
    notes: str = ""


def normalize_url(url: str) -> str:
    """Strip tracking params and fragment for dedupe comparison.

    Removes utm_*, ref, source, fbclid, gclid query params and the
    fragment hash. Preserves path and other query params.
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    cleaned = {
        k: v
        for k, v in params.items()
        if not any(k.lower().startswith(p) for p in _TRACKING_PREFIXES)
    }
    clean_query = urlencode(cleaned, doseq=True)
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, clean_query, "")
    )


def init_log(path: Path | str) -> Path:
    """Create the CSV with header if it doesn't exist. Idempotent.

    Args:
        path: Path to the applications_log.csv file.

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


def is_duplicate(platform: str, job_url: str, log_path: Path | str) -> bool:
    """Check if (platform, normalised_url) already exists in the log.

    Normalises URLs by stripping tracking params and fragments so the same
    job with different UTM tags is caught as a duplicate.

    Args:
        platform: e.g. "naukri".
        job_url: The job URL to check.
        log_path: Path to applications_log.csv.

    Returns:
        True if this job was already logged.
    """
    log_path = Path(log_path)
    if not log_path.exists():
        return False

    target_platform = platform.lower()
    target_url = normalize_url(job_url)

    with open(log_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_platform = row.get("platform", "").strip().lower()
            row_url = normalize_url(row.get("job_url", ""))
            if row_platform == target_platform and row_url == target_url:
                return True
    return False


def log_application(entry: LogEntry, log_path: Path | str) -> None:
    """Append one row to applications_log.csv. Uses file locking for atomicity.

    Args:
        entry: A LogEntry dataclass instance.
        log_path: Path to applications_log.csv.
    """
    log_path = Path(log_path)
    init_log(log_path)

    row = asdict(entry)
    # Format fit_score to 2 decimal places
    row["fit_score"] = f"{entry.fit_score:.2f}"

    with open(log_path, "a", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writerow(row)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def count_today(platform: str, log_path: Path | str) -> int:
    """Count today's successful applications for a platform.

    Counts rows where status is 'applied' or 'applied_unconfirmed'
    for today's date (UTC).

    Args:
        platform: e.g. "naukri".
        log_path: Path to applications_log.csv.

    Returns:
        Number of applications today for this platform.
    """
    log_path = Path(log_path)
    if not log_path.exists():
        return 0

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    target_platform = platform.lower()
    count = 0

    with open(log_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (
                row.get("date_applied") == today
                and row.get("platform", "").strip().lower() == target_platform
                and row.get("status") in ("applied", "applied_unconfirmed")
            ):
                count += 1
    return count


def read_log(log_path: Path | str) -> list[LogEntry]:
    """Read all entries from the log as a list of LogEntry.

    Args:
        log_path: Path to applications_log.csv.

    Returns:
        List of LogEntry objects. Empty list if file doesn't exist.
    """
    log_path = Path(log_path)
    if not log_path.exists():
        return []

    entries: list[LogEntry] = []
    with open(log_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            entries.append(LogEntry(
                date_applied=row.get("date_applied", ""),
                time_applied=row.get("time_applied", ""),
                platform=row.get("platform", ""),
                company_name=row.get("company_name", ""),
                role_title=row.get("role_title", ""),
                experience_required=row.get("experience_required", ""),
                location=row.get("location", ""),
                job_url=row.get("job_url", ""),
                fit_score=float(row.get("fit_score", 0)),
                status=row.get("status", ""),
                notes=row.get("notes", ""),
            ))
    return entries
