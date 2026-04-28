"""CSV logger, dedupe engine, and review-queue writer.

All CSV writes in the project go through this module — no platform writes
its own CSV (CLAUDE.md §7). Schemas match CLAUDE.md §9 and guidelines.md §3.6.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)

# --- CSV column headers (source of truth for schema) ---

APPLICATION_LOG_COLUMNS = [
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

DAILY_SUMMARY_COLUMNS = [
    "date",
    "total_applied",
    "naukri_applied",
    "linkedin_applied",
    "wellfound_applied",
    "cutshort_applied",
    "total_skipped",
    "total_errors",
    "runtime_seconds",
]

REVIEW_QUEUE_COLUMNS = [
    "queued_at",
    "platform",
    "company_name",
    "role_title",
    "job_url",
    "fit_score",
    "tier",
    "reason_queued",
    "custom_questions",
    "expires_at",
]

# Tracking params stripped during URL normalization
_TRACKING_PREFIXES = ("utm_", "ref", "source", "fbclid", "gclid")


def normalize_url(url: str) -> str:
    """Strip tracking params and fragment from a job URL for dedupe.

    Removes utm_*, ref, source, fbclid, gclid query params and the
    fragment hash. Preserves everything else.
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


def _ensure_csv(path: Path, columns: list[str]) -> None:
    """Create a CSV file with headers if it doesn't exist."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
        logger.info("Created %s with headers", path)


class ApplicationLogger:
    """Handles all CSV logging and deduplication.

    Args:
        log_file: Path to applications_log.csv.
        summary_file: Path to daily_summary.csv.
        queue_file: Path to data/review_queue.csv.
    """

    def __init__(
        self,
        log_file: Path | str = "data/applications_log.csv",
        summary_file: Path | str = "data/daily_summary.csv",
        queue_file: Path | str = "data/review_queue.csv",
    ) -> None:
        self.log_file = Path(log_file)
        self.summary_file = Path(summary_file)
        self.queue_file = Path(queue_file)

        _ensure_csv(self.log_file, APPLICATION_LOG_COLUMNS)
        _ensure_csv(self.summary_file, DAILY_SUMMARY_COLUMNS)
        _ensure_csv(self.queue_file, REVIEW_QUEUE_COLUMNS)

        self._dedupe_set: set[tuple[str, str]] = set()
        self._load_dedupe_set()

    def _load_dedupe_set(self) -> None:
        """Build in-memory dedupe set from existing application log."""
        try:
            with open(self.log_file, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    platform = row.get("platform", "").strip().lower()
                    url = normalize_url(row.get("job_url", ""))
                    if platform and url:
                        self._dedupe_set.add((platform, url))
        except FileNotFoundError:
            pass
        logger.info("Dedupe set loaded: %d entries", len(self._dedupe_set))

    def is_duplicate(self, platform: str, job_url: str) -> bool:
        """Check if this (platform, url) pair has already been logged."""
        return (platform.lower(), normalize_url(job_url)) in self._dedupe_set

    def log_application(
        self,
        platform: str,
        company_name: str,
        role_title: str,
        experience_required: str,
        location: str,
        job_url: str,
        fit_score: float,
        status: str,
        notes: str = "",
    ) -> None:
        """Append one row to applications_log.csv and update dedupe set.

        Args:
            platform: e.g. 'naukri', 'linkedin'.
            company_name: Company name from the listing.
            role_title: Job title from the listing.
            experience_required: e.g. '3-5 years'.
            location: e.g. 'Bangalore'.
            job_url: Full URL to the job listing.
            fit_score: Computed score 0.0-1.0.
            status: One of: applied, applied_unconfirmed, skipped, queued, error.
            notes: Reason for skip/error, or empty string.
        """
        now = datetime.now(timezone.utc)
        row = {
            "date_applied": now.strftime("%Y-%m-%d"),
            "time_applied": now.strftime("%H:%M"),
            "platform": platform.lower(),
            "company_name": company_name,
            "role_title": role_title,
            "experience_required": experience_required,
            "location": location,
            "job_url": job_url,
            "fit_score": f"{fit_score:.2f}",
            "status": status,
            "notes": notes,
        }
        with open(self.log_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=APPLICATION_LOG_COLUMNS)
            writer.writerow(row)

        # Update in-memory dedupe set
        self._dedupe_set.add((platform.lower(), normalize_url(job_url)))
        logger.debug("Logged: %s at %s [%s]", role_title, company_name, status)

    def log_to_review_queue(
        self,
        platform: str,
        company_name: str,
        role_title: str,
        job_url: str,
        fit_score: float,
        tier: str,
        reason: str,
        custom_questions: list[dict] | None = None,
    ) -> None:
        """Append a Yellow-tier job to the review queue.

        Args:
            platform: e.g. 'naukri'.
            company_name: Company name.
            role_title: Job title.
            job_url: Full URL.
            fit_score: Computed score.
            tier: T1/T2/T3.
            reason: One of: score_in_review_band, custom_questions,
                    priority_company, first_time_platform.
            custom_questions: List of {question, suggested_answer} dicts.
        """
        now = datetime.now(timezone.utc)
        # Queue items expire after 5 days (guidelines.md §3.6)
        from datetime import timedelta

        expires = now + timedelta(days=5)
        row = {
            "queued_at": now.isoformat(),
            "platform": platform.lower(),
            "company_name": company_name,
            "role_title": role_title,
            "job_url": job_url,
            "fit_score": f"{fit_score:.2f}",
            "tier": tier,
            "reason_queued": reason,
            "custom_questions": json.dumps(custom_questions or []),
            "expires_at": expires.isoformat(),
        }
        with open(self.queue_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=REVIEW_QUEUE_COLUMNS)
            writer.writerow(row)
        logger.info("Queued for review: %s at %s (%s)", role_title, company_name, reason)

    def write_daily_summary(self, stats: dict) -> None:
        """Append today's summary row to daily_summary.csv.

        Args:
            stats: Dict with keys matching DAILY_SUMMARY_COLUMNS.
                   Missing keys default to 0.
        """
        row = {col: stats.get(col, 0) for col in DAILY_SUMMARY_COLUMNS}
        row["date"] = stats.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        with open(self.summary_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=DAILY_SUMMARY_COLUMNS)
            writer.writerow(row)
        logger.info("Daily summary written for %s", row["date"])

    def get_today_counts(self, date: str | None = None) -> dict[str, int]:
        """Count today's applications per platform from the log.

        Returns:
            Dict like {'naukri': 42, 'linkedin': 15, ...}.
        """
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        counts: dict[str, int] = {}
        try:
            with open(self.log_file, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("date_applied") == date and row.get("status") in (
                        "applied",
                        "applied_unconfirmed",
                    ):
                        platform = row.get("platform", "unknown")
                        counts[platform] = counts.get(platform, 0) + 1
        except FileNotFoundError:
            pass
        return counts

    def get_recent_error_rate(self, platform: str, hours: int = 24) -> float:
        """Compute error rate for a platform over the last N hours.

        Returns:
            Float between 0.0 and 1.0. Returns 0.0 if no attempts found.
        """
        cutoff = datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=hours)
        total = 0
        errors = 0
        try:
            with open(self.log_file, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("platform", "").lower() != platform.lower():
                        continue
                    try:
                        row_date = datetime.strptime(
                            f"{row['date_applied']} {row['time_applied']}",
                            "%Y-%m-%d %H:%M",
                        ).replace(tzinfo=timezone.utc)
                    except (KeyError, ValueError):
                        continue
                    if row_date >= cutoff:
                        total += 1
                        if row.get("status") == "error":
                            errors += 1
        except FileNotFoundError:
            pass
        return errors / total if total > 0 else 0.0
