"""Daily email digest sender.

Builds and delivers a plain-text summary of the day's run per
guidelines.md §3.7. Call send_digest() directly or via
`python apply.py --email-summary-only [--dry-email]`.
"""

from __future__ import annotations

import csv
import logging
import os
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SMTPConfig:
    """SMTP connection settings loaded from .env."""

    host: str
    port: int
    user: str
    password: str
    digest_to: str


def build_smtp_config() -> Optional[SMTPConfig]:
    """Read SMTP settings from env. Returns None if any required field is empty.

    Required env vars: SMTP_HOST, SMTP_USER, SMTP_PASSWORD, DIGEST_TO.
    Optional: SMTP_PORT (default 587).
    """
    host = os.getenv("SMTP_HOST", "")
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    digest_to = os.getenv("DIGEST_TO", "")

    missing = [name for name, val in [
        ("SMTP_HOST", host),
        ("SMTP_USER", user),
        ("SMTP_PASSWORD", password),
        ("DIGEST_TO", digest_to),
    ] if not val]

    if missing:
        logger.warning("SMTP config incomplete — missing env vars: %s", ", ".join(missing))
        return None

    return SMTPConfig(
        host=host,
        port=int(os.getenv("SMTP_PORT", "587")),
        user=user,
        password=password,
        digest_to=digest_to,
    )


# ── Data loaders ───────────────────────────────────────────────────────


def _load_summary_row(summary_path: Path, date_str: str) -> dict:
    """Return the summary CSV row for date_str, or {} if not found."""
    if not summary_path.exists():
        return {}
    with open(summary_path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("date") == date_str:
                return dict(row)
    return {}


def _load_top_applies(log_path: Path, date_str: str, n: int = 10) -> list[dict]:
    """Return up to n applied entries for date_str sorted by fit_score desc."""
    if not log_path.exists():
        return []
    rows = []
    with open(log_path, newline="") as f:
        for row in csv.DictReader(f):
            if (
                row.get("date_applied") == date_str
                and row.get("status") in ("applied", "applied_unconfirmed")
            ):
                rows.append(dict(row))
    rows.sort(key=lambda r: float(r.get("fit_score") or 0), reverse=True)
    return rows[:n]


def _load_errors(log_path: Path, date_str: str) -> list[dict]:
    """Return all error-status entries for date_str."""
    if not log_path.exists():
        return []
    with open(log_path, newline="") as f:
        return [
            dict(row)
            for row in csv.DictReader(f)
            if row.get("date_applied") == date_str and row.get("status") == "error"
        ]


def _load_queue(queue_path: Path, preview: int = 5) -> tuple[int, list[dict]]:
    """Return (total_count, first preview items) from review_queue.csv.

    Returns (0, []) if the file doesn't exist — queue is empty until the
    first Yellow-tier job is encountered.
    """
    if not queue_path.exists():
        return 0, []
    rows = []
    with open(queue_path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append(dict(row))
    return len(rows), rows[:preview]


# ── Body builder ───────────────────────────────────────────────────────


def _build_body(
    date_str: str,
    summary: dict,
    prev_summary: dict,
    top_applies: list[dict],
    queue_total: int,
    queue_items: list[dict],
    errors: list[dict],
) -> str:
    """Assemble the five-section plain-text email body."""
    lines: list[str] = []

    def _int(d: dict, key: str) -> int:
        try:
            return int(d.get(key) or 0)
        except (ValueError, TypeError):
            return 0

    total_applied = _int(summary, "total_applied")
    total_skipped = _int(summary, "total_skipped")
    total_errors = _int(summary, "total_errors")
    runtime = _int(summary, "runtime_seconds")

    # ── 1. Header / summary ────────────────────────────────────────────
    sep = "=" * 48
    lines += [
        f"Job bot daily digest — {date_str}",
        sep,
        "",
        "SUMMARY",
        "-------",
        f"Date:          {date_str}",
        f"Total applied: {total_applied}",
        "",
        f"  Naukri:      {_int(summary, 'naukri_applied')}",
        f"  LinkedIn:    {_int(summary, 'linkedin_applied')}",
        f"  Wellfound:   {_int(summary, 'wellfound_applied')}",
        f"  Cutshort:    {_int(summary, 'cutshort_applied')}",
        "",
        f"Skipped:       {total_skipped}",
        f"Errors:        {total_errors}",
        f"Runtime:       {runtime}s",
        "",
    ]

    # ── 2. Top 10 applies by fit score ─────────────────────────────────
    lines += [
        "TOP 10 APPLIES BY FIT SCORE",
        "----------------------------",
    ]
    if top_applies:
        header = f"  {'#':<3} {'Company':<22} {'Role':<32} {'Score'}  URL"
        lines.append(header)
        lines.append("  " + "-" * 80)
        for i, row in enumerate(top_applies, 1):
            company = (row.get("company_name") or "")[:22]
            role = (row.get("role_title") or "")[:32]
            score = float(row.get("fit_score") or 0)
            url = row.get("job_url") or ""
            lines.append(f"  {i:<3} {company:<22} {role:<32} {score:.2f}  {url}")
    else:
        lines.append("  No applications recorded for this date.")
    lines.append("")

    # ── 3. Yellow queue ────────────────────────────────────────────────
    lines += [
        f"YELLOW QUEUE — NEEDS REVIEW ({queue_total} item{'s' if queue_total != 1 else ''})",
        "-" * 48,
    ]
    if queue_total == 0:
        lines.append("  Queue is empty — nothing to review.")
    else:
        shown = len(queue_items)
        lines.append(
            f"  {queue_total} item(s) awaiting review."
            + (f" First {shown}:" if queue_total > shown else "")
        )
        lines.append("")
        for i, item in enumerate(queue_items, 1):
            company = item.get("company_name") or ""
            role = item.get("role_title") or ""
            score = float(item.get("fit_score") or 0)
            tier = item.get("tier") or "?"
            url = item.get("job_url") or ""
            reason = item.get("reason_queued") or ""
            lines.append(
                f"  {i}. [{tier}] {company} — {role}"
                f"  (score: {score:.2f}, reason: {reason})"
            )
            lines.append(f"     {url}")
    lines.append("")
    lines.append("  Run `python apply.py --review-queue` to process.")
    lines.append("")

    # ── 4. Errors / throttles ──────────────────────────────────────────
    lines += [
        "ERRORS & THROTTLES",
        "------------------",
    ]
    if not errors:
        lines.append("  No errors today.")
    else:
        lines.append(f"  {len(errors)} error(s) today:")
        lines.append("")
        for row in errors[:5]:
            platform = row.get("platform") or "?"
            company = row.get("company_name") or "(unknown)"
            role = row.get("role_title") or "(unknown)"
            notes = row.get("notes") or ""
            lines.append(f"  [{platform}] {company} — {role}")
            if notes:
                lines.append(f"    → {notes}")
        if len(errors) > 5:
            lines.append(f"  ... and {len(errors) - 5} more. See data/applications_log.csv.")
    lines.append("")

    # ── 5. Yesterday-vs-today delta ────────────────────────────────────
    lines += [
        "YESTERDAY vs TODAY DELTA",
        "------------------------",
    ]
    if not prev_summary:
        lines.append("  No data for yesterday.")
    else:
        prev_applied = _int(prev_summary, "total_applied")
        delta = total_applied - prev_applied
        sign = "+" if delta >= 0 else ""
        lines.append(f"  Yesterday: {prev_applied}")
        lines.append(f"  Today:     {total_applied}")
        if prev_applied > 0:
            pct = delta / prev_applied * 100
            lines.append(f"  Change:    {sign}{delta} ({sign}{pct:.0f}%)")
        else:
            lines.append(f"  Change:    {sign}{delta}")
    lines.append("")
    lines.append("-" * 48)
    lines.append("Generated by job-bot.")

    return "\n".join(lines)


# ── Public API ─────────────────────────────────────────────────────────


def send_digest(
    date: str,
    log_path: Path,
    summary_path: Path,
    queue_path: Path,
    smtp_config: Optional[SMTPConfig],
    print_only: bool = False,
) -> None:
    """Build and send (or print) the daily email digest.

    Args:
        date: ISO date string, e.g. "2026-04-28".
        log_path: Path to applications_log.csv.
        summary_path: Path to daily_summary.csv.
        queue_path: Path to data/review_queue.csv.
        smtp_config: SMTP settings from build_smtp_config(). If None,
            logs a warning and returns — no crash.
        print_only: If True, print subject + body to stdout; don't send.
            Use with --dry-email to inspect the email before going live.
    """
    if smtp_config is None:
        if not print_only:
            logger.warning("No SMTP config — digest not sent (set SMTP_* vars in .env)")
            return
        # Still render and print — useful for previewing before creds are set
        smtp_config = SMTPConfig(
            host="", port=587, user="<not configured>", password="", digest_to="<not configured>"
        )

    # Load all data sources (each handles missing files gracefully)
    summary = _load_summary_row(summary_path, date)
    if not summary:
        logger.warning(
            "No summary row for %s in %s — digest will show zeros", date, summary_path
        )

    prev_date = (
        datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)
    ).strftime("%Y-%m-%d")
    prev_summary = _load_summary_row(summary_path, prev_date)

    top_applies = _load_top_applies(log_path, date)
    queue_total, queue_items = _load_queue(queue_path)
    errors = _load_errors(log_path, date)

    total_applied = int(summary.get("total_applied") or 0)
    subject = (
        f"Job bot daily digest — {date} — "
        f"{total_applied} applies, {queue_total} to review"
    )
    body = _build_body(
        date_str=date,
        summary=summary,
        prev_summary=prev_summary,
        top_applies=top_applies,
        queue_total=queue_total,
        queue_items=queue_items,
        errors=errors,
    )

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
        logger.info("Digest sent to %s", smtp_config.digest_to)
    except smtplib.SMTPAuthenticationError as exc:
        logger.error(
            "SMTP authentication failed — check SMTP_USER / SMTP_PASSWORD: %s", exc
        )
    except smtplib.SMTPConnectError as exc:
        logger.error(
            "SMTP connect failed (%s:%d): %s", smtp_config.host, smtp_config.port, exc
        )
    except smtplib.SMTPException as exc:
        logger.error("SMTP error sending digest: %s", exc)
    except OSError as exc:
        logger.error("Network error sending digest: %s", exc)
