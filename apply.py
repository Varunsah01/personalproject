#!/usr/bin/env python3
"""Main runner for job-bot.

Orchestrates the full daily pipeline: pre-flight checks, sequential
platform runs, post-run summary. See CLAUDE.md §6 and §10 for the
daily routine and CLI flags. Behaviour rules in guidelines.md §3.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import importlib
import logging
import os
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

import json
import webbrowser

from core.logger import LogEntry, count_today, init_log, is_duplicate, log_application, read_log
from core.notifier import build_smtp_config, send_digest
from core.orchestrator import Candidate, count_today_all, rank_and_allocate
from core.relevance_agent import RelevanceAgent
from core.scorer import Job, classify_tier, score_job, should_apply
from core.types import BotConfig

logger = logging.getLogger(__name__)


# ── Keywords (CLAUDE.md §4) ────────────────────────────────────────────

PRIMARY_KEYWORDS: list[str] = [
    "growth manager",
    "product manager",
    "strategy and operations",
    "business development manager",
    "founding team",
    "founding member",
    "GTM manager",
    "head of growth",
    "early employee",
]

SECONDARY_KEYWORDS: list[str] = [
    "partnerships manager",
    "revenue operations",
    "VC analyst",
    "chief of staff",
    "program manager",
    "associate product manager",
]


# ── Filters (CLAUDE.md §5) ─────────────────────────────────────────────

FILTERS: dict = {
    "locations": ["Delhi NCR", "Bangalore", "Mumbai", "Remote India"],
    "experience_min": 1,
    "experience_max": 5,
    "date_max_age_days": 7,  # Naukri/LinkedIn; Wellfound/Cutshort use 14
}


# ── Platform config (CLAUDE.md §2) ────────────────────────────────────

# Sequential order for the daily run
PLATFORM_ORDER: list[str] = ["naukri", "linkedin", "wellfound", "cutshort", "greenhouse", "instahyre"]

# Global daily cap — total applications across all platforms
DAILY_CAP_GLOBAL_DEFAULT: int = 300

# Per-platform soft ceilings — anti-rate-limit guardrails
DAILY_CEILINGS_DEFAULT: dict[str, int] = {
    "naukri": 100,
    "linkedin": 100,
    "wellfound": 100,
    "cutshort": 100,
    "greenhouse": 80,
    "instahyre": 100,
}

# Env var pairs (email_key, password_key) per platform
CREDENTIAL_KEYS: dict[str, tuple[str, str]] = {
    "naukri": ("NAUKRI_EMAIL", "NAUKRI_PASSWORD"),
    "linkedin": ("LINKEDIN_EMAIL", "LINKEDIN_PASSWORD"),
    "wellfound": ("WELLFOUND_EMAIL", "WELLFOUND_PASSWORD"),
    "cutshort": ("CUTSHORT_EMAIL", "CUTSHORT_PASSWORD"),
    "instahyre": ("INSTAHYRE_EMAIL", "INSTAHYRE_PASSWORD"),
}


# ── Paths and thresholds ───────────────────────────────────────────────

LOG_PATH = Path(os.getenv("LOG_FILE", "data/applications_log.csv"))
SUMMARY_PATH = Path(os.getenv("SUMMARY_FILE", "data/daily_summary.csv"))
RESUME_PATH = Path("Varun_Sah_CV.pdf")
STOP_FILE = Path("data/STOP")
QUEUE_PATH = Path("data/review_queue.csv")

REVIEW_QUEUE_COLUMNS = [
    "queued_at", "platform", "company_name", "role_title", "job_url",
    "fit_score", "tier", "reason_queued", "custom_questions", "expires_at",
]

MIN_FREE_DISK_MB = 500          # guidelines.md §6
RESUME_MAX_AGE_DAYS = 60        # guidelines.md §3.1
MAX_TOTAL_ERRORS = 50           # guidelines.md §6
ERROR_RATE_THRESHOLD = 0.25     # guidelines.md §3.1 + §6

SUMMARY_COLUMNS = [
    "date", "total_discovered", "total_applied",
    "naukri_discovered", "naukri_applied",
    "linkedin_discovered", "linkedin_applied",
    "wellfound_discovered", "wellfound_applied",
    "cutshort_discovered", "cutshort_applied",
    "greenhouse_discovered", "greenhouse_applied",
    "instahyre_discovered", "instahyre_applied",
    "total_skipped", "total_errors", "runtime_seconds",
]

# Common form-field answers shared by all platforms (from profile.md).
# Platform subclasses add email and CTC from platform-specific env vars.
STANDARD_ANSWERS_BASE: dict[str, str] = {
    "name": "Varun Sah",
    "phone": "+91-8595062552",
    "location": "Delhi NCR",
    "notice_period": "Immediate",
    "years_of_experience": "4",
    "linkedin": "https://www.linkedin.com/in/varun-sah/",
}


def _build_config(name: str, headless: bool, dry_run: bool) -> BotConfig:
    """Build a BotConfig for a platform using .env values.

    The daily_cap field carries the per-platform soft ceiling (used by
    single-platform legacy mode and as a safety valve inside
    _attempt_apply).
    """
    ceiling = int(os.getenv(
        f"DAILY_CAP_{name.upper()}_CEILING",
        str(DAILY_CEILINGS_DEFAULT.get(name, 100)),
    ))
    return BotConfig(
        log_path=LOG_PATH,
        headless=headless,
        dry_run=dry_run,
        daily_cap=ceiling,
        standard_answers=dict(STANDARD_ANSWERS_BASE),
    )


def _create_platform(cls, config: BotConfig):
    """Construct a platform instance.

    BotConfig-aware platforms (naukri) receive the config directly.
    Legacy platforms (linkedin, wellfound, cutshort) still use keyword
    args until they are migrated to BotConfig.
    """
    import inspect

    if "config" in inspect.signature(cls.__init__).parameters:
        return cls(config)
    # Legacy construction — migration pending
    return cls(log_path=config.log_path, headless=config.headless, dry_run=config.dry_run)


# ── Platform loader ────────────────────────────────────────────────────

def _load_platform_class(name: str):
    """Dynamically import a platform class from platforms/__init__.py registry.

    Raises ImportError or AttributeError if the module or class doesn't exist.
    """
    from platforms import PLATFORM_REGISTRY
    dotted = PLATFORM_REGISTRY.get(name)
    if not dotted:
        raise ValueError(f"Unknown platform: {name!r} (not in PLATFORM_REGISTRY)")
    module_path, class_name = dotted.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


# ── Pre-flight ─────────────────────────────────────────────────────────

def _preflight(platform_names: list[str], log_path: Path) -> dict[str, str | None]:
    """Run pre-flight checks per guidelines.md §3.1.

    Returns:
        Dict mapping each platform name to None (OK) or a skip-reason string.
    """
    results: dict[str, str | None] = {}

    # 1a. Resume existence and age
    if not RESUME_PATH.exists():
        logger.warning("Resume not found at %s — resume uploads will be skipped", RESUME_PATH)
    else:
        age_days = (time.time() - RESUME_PATH.stat().st_mtime) / 86400
        if age_days > RESUME_MAX_AGE_DAYS:
            logger.warning(
                "Resume is %.0f days old (limit: %d) — consider refreshing before running",
                age_days, RESUME_MAX_AGE_DAYS,
            )

    # 1b. Disk space on the data/ volume (guidelines.md §6)
    try:
        data_dir = log_path.parent
        data_dir.mkdir(parents=True, exist_ok=True)
        free_mb = shutil.disk_usage(data_dir).free / (1024 * 1024)
        if free_mb < MIN_FREE_DISK_MB:
            logger.error(
                "Disk space critically low: %.0f MB free (limit: %d MB) — aborting all platforms",
                free_mb, MIN_FREE_DISK_MB,
            )
            for name in platform_names:
                results[name] = f"skip: disk space low ({free_mb:.0f} MB free)"
            return results
        logger.debug("Disk space OK: %.0f MB free", free_mb)
    except Exception as exc:
        logger.warning("Could not check disk space: %s", exc)

    # 1c. Global cap check — if total applies today already hit the global
    # cap there is nothing useful to do.
    global_cap = int(os.getenv("DAILY_CAP_GLOBAL", str(DAILY_CAP_GLOBAL_DEFAULT)))
    total_today = sum(count_today(n, log_path) for n in platform_names)
    if total_today >= global_cap:
        reason = f"skip: global daily cap reached ({total_today}/{global_cap})"
        logger.warning(reason)
        for name in platform_names:
            results[name] = reason
        return results

    # Per-platform checks
    for name in platform_names:
        # 2. Credentials present (empty → login will fail; warn early)
        keys = CREDENTIAL_KEYS.get(name, ())
        missing = [k for k in keys if not os.getenv(k)]
        if missing:
            logger.warning(
                "[%s] Missing env vars: %s — login will fail immediately",
                name, ", ".join(missing),
            )
            # Don't skip — let login() surface the failure so it lands in the log.

        # 3. Already at per-platform ceiling from a previous run today?
        ceiling = int(os.getenv(
            f"DAILY_CAP_{name.upper()}_CEILING",
            str(DAILY_CEILINGS_DEFAULT.get(name, 100)),
        ))
        today_count = count_today(name, log_path)
        if today_count >= ceiling:
            reason = f"skip: already at platform ceiling ({today_count}/{ceiling})"
            logger.warning("[%s] %s", name, reason)
            results[name] = reason
            continue

        # 4. Error rate in the last 24 hours (guidelines.md §3.1 step 4)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        entries = read_log(log_path)
        recent: list = []
        for e in entries:
            if e.platform.lower() != name:
                continue
            try:
                ts = datetime.strptime(
                    f"{e.date_applied} {e.time_applied}", "%Y-%m-%d %H:%M"
                ).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if ts >= cutoff:
                recent.append(e)

        if recent:
            errors = sum(1 for e in recent if e.status == "error")
            error_rate = errors / len(recent)
            if error_rate > ERROR_RATE_THRESHOLD:
                reason = (
                    f"skip: error rate {error_rate:.0%} > {ERROR_RATE_THRESHOLD:.0%}"
                    f" in last 24h ({errors}/{len(recent)} attempts errored)"
                )
                logger.warning("[%s] %s", name, reason)
                results[name] = reason
                continue

        results[name] = None  # all checks passed

    return results


# ── Post-run ───────────────────────────────────────────────────────────

def _write_summary(
    stats: dict[str, dict],
    runtime_seconds: float,
    summary_path: Path,
) -> None:
    """Append one row to daily_summary.csv (CLAUDE.md §9)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not summary_path.exists()

    def _applied(name: str) -> int:
        s = stats.get(name, {})
        return s.get("applied", 0) + s.get("applied_unconfirmed", 0)

    def _discovered(name: str) -> int:
        return stats.get(name, {}).get("discovered", 0)

    row = {
        "date": today,
        "total_discovered": sum(_discovered(n) for n in PLATFORM_ORDER),
        "total_applied": sum(_applied(n) for n in PLATFORM_ORDER),
        "naukri_discovered": _discovered("naukri"),
        "naukri_applied": _applied("naukri"),
        "linkedin_discovered": _discovered("linkedin"),
        "linkedin_applied": _applied("linkedin"),
        "wellfound_discovered": _discovered("wellfound"),
        "wellfound_applied": _applied("wellfound"),
        "cutshort_discovered": _discovered("cutshort"),
        "cutshort_applied": _applied("cutshort"),
        "greenhouse_discovered": _discovered("greenhouse"),
        "greenhouse_applied": _applied("greenhouse"),
        "instahyre_discovered": _discovered("instahyre"),
        "instahyre_applied": _applied("instahyre"),
        "total_skipped": sum(stats.get(n, {}).get("skipped", 0) for n in PLATFORM_ORDER),
        "total_errors": sum(stats.get(n, {}).get("errored", 0) for n in PLATFORM_ORDER),
        "runtime_seconds": int(runtime_seconds),
    }

    with open(summary_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def _print_summary(
    stats: dict[str, dict],
    runtime_seconds: float,
    skipped_platforms: dict[str, str],
) -> None:
    """Print the post-run summary table to stdout (guidelines.md §3.7)."""
    width = 60
    print()
    print("─" * width)
    print(f"  job-bot run complete — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("─" * width)
    print(f"  {'Platform':<12} {'Found':>7} {'Applied':>7} {'Skipped':>8} {'Errors':>7} {'Queued':>7}")
    print("─" * width)

    total_discovered = total_applied = total_skipped = total_errors = 0

    for name in PLATFORM_ORDER:
        if name in skipped_platforms:
            reason = skipped_platforms[name].replace("skip: ", "")
            print(f"  {name:<12} {'—':>7} {'—':>7} {'—':>8} {'—':>7} {'—':>7}  ({reason})")
            continue
        if name not in stats:
            continue
        s = stats[name]
        discovered = s.get("discovered", 0)
        applied = s.get("applied", 0) + s.get("applied_unconfirmed", 0)
        skipped = s.get("skipped", 0)
        errors = s.get("errored", 0)
        queued = s.get("queued", 0)
        print(f"  {name:<12} {discovered:>7} {applied:>7} {skipped:>8} {errors:>7} {queued:>7}")
        total_discovered += discovered
        total_applied += applied
        total_skipped += skipped
        total_errors += errors

    print("─" * width)
    print(f"  {'TOTAL':<12} {total_discovered:>7} {total_applied:>7} {total_skipped:>8} {total_errors:>7}")
    print(f"\n  Runtime: {runtime_seconds:.0f}s")
    print("─" * width)
    print()


# ── Review-queue handler ───────────────────────────────────────────────


def _read_queue(path: Path) -> list[dict]:
    """Read all entries from the review queue CSV."""
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _write_queue(path: Path, entries: list[dict]) -> None:
    """Rewrite the review queue CSV with the given entries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REVIEW_QUEUE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(entries)


def _is_expired(entry: dict) -> bool:
    """Return True if the queue entry's expires_at is in the past."""
    raw = entry.get("expires_at", "")
    if not raw:
        return False
    try:
        exp = datetime.fromisoformat(raw)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > exp
    except ValueError:
        return False


def _display_item(idx: int, total: int, item: dict) -> None:
    """Print a formatted header for one review-queue item."""
    sep = "─" * 56
    company = item.get("company_name") or "(unknown)"
    role = item.get("role_title") or "(unknown)"
    tier = item.get("tier") or "?"
    print()
    print(sep)
    print(f"  Item {idx} / {total}  [{tier}] {company} — {role}")
    print(sep)
    score = item.get("fit_score") or "?"
    platform = item.get("platform") or "?"
    reason = item.get("reason_queued") or "?"
    queued_at = (item.get("queued_at") or "")[:10]
    expires_at = (item.get("expires_at") or "")[:10]
    url = item.get("job_url") or ""
    print(f"  Platform: {platform:<10} Score: {score:<6} Tier: {tier}")
    print(f"  Reason:   {reason}")
    print(f"  Queued:   {queued_at}  (expires {expires_at})")
    print(f"  URL:      {url}")


def _edit_answers(custom_qs: list[dict]) -> dict:
    """Show each custom question and its suggested answer; let user edit.

    Returns:
        {question_text: final_answer} — preserving the original text as key.
    """
    print()
    print("  Custom questions — edit your answer (Enter to keep suggested):")
    print()
    answers: dict[str, str] = {}
    for i, q in enumerate(custom_qs, 1):
        question = q.get("question") or q.get("label") or f"Question {i}"
        suggested = q.get("suggested_answer") or ""
        suggested_display = suggested if suggested else "(empty)"
        print(f"  Q{i}: {question}")
        print(f"  Suggested: {suggested_display}")
        try:
            user_input = input("  Your answer: ").strip()
        except (EOFError, KeyboardInterrupt):
            user_input = ""
        final = user_input if user_input else suggested
        answers[question] = final
        print()
    return answers


def _prompt_action() -> str:
    """Prompt for the review action and return the chosen char."""
    valid = {"a", "s", "d", "n", "q"}
    print("  [a]pply  [s]kip  [d]raft (open in browser)  [n]ext (leave in queue)  [q]uit")
    while True:
        try:
            choice = input("  Action: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "q"
        if choice in valid:
            return choice
        print(f"  Invalid choice '{choice}' — enter a, s, d, n, or q.")


def _log_queue_entry(item: dict, status: str, notes: str) -> None:
    """Log a queue item directly to applications_log.csv (no browser needed)."""
    now = datetime.now(timezone.utc)
    entry = LogEntry(
        date_applied=now.strftime("%Y-%m-%d"),
        time_applied=now.strftime("%H:%M"),
        platform=item.get("platform") or "",
        company_name=item.get("company_name") or "",
        role_title=item.get("role_title") or "",
        experience_required="",
        location="",
        job_url=item.get("job_url") or "",
        fit_score=float(item.get("fit_score") or 0),
        status=status,
        notes=notes,
    )
    log_application(entry, LOG_PATH)


async def _queue_apply(item: dict, answers: dict, args: argparse.Namespace) -> bool:
    """Launch a browser, login, and apply to one review-queue job.

    Args:
        item: One row from review_queue.csv as a dict.
        answers: {question_text: answer} from _edit_answers().
        args: Parsed CLI args (used for headless flag).

    Returns:
        True if application was submitted (applied or applied_unconfirmed).
    """
    name = item.get("platform") or ""
    try:
        cls = _load_platform_class(name)
    except Exception as exc:
        logger.error("Cannot load platform '%s': %s", name, exc)
        return False

    headless = os.getenv("HEADLESS", "true").lower() != "false"
    config = _build_config(name, headless=headless, dry_run=False)
    platform = _create_platform(cls, config)

    # Reconstruct a minimal Job — URL is stored in posted_date per platform convention
    job = Job(
        title=item.get("role_title") or "",
        company=item.get("company_name") or "",
        location="",
        experience_required="",
        jd_text="",
        posted_date=item.get("job_url") or "",
    )
    fit_score = float(item.get("fit_score") or 0)
    tier = item.get("tier") or "T1"

    logged_in = await platform.login()
    if not logged_in:
        print(f"  Login failed for {name} — skipping apply.")
        return False

    status = "error"
    notes = "apply not attempted"
    try:
        result = await platform.score_and_apply(job, fit_score, tier, answers)
        status = result.get("status") or "error"
        notes = result.get("notes") or ""
        print(f"  Result: {status}" + (f" — {notes}" if notes else ""))
    except Exception as exc:
        logger.error("Error during queue apply for %s/%s: %s", name, item.get("job_url"), exc, exc_info=True)
        notes = f"review apply error: {exc}"
    finally:
        try:
            await platform.logout()
        except Exception:
            pass

    _log_queue_entry(item, status, notes)
    return status in ("applied", "applied_unconfirmed")


def _handle_review_queue(args: argparse.Namespace) -> None:
    """Interactive review-queue handler. Per guidelines.md §3.6."""
    entries = _read_queue(QUEUE_PATH)
    if not entries:
        print("Queue is empty — no items to review.")
        return

    # Partition into expired and active
    active: list[dict] = []
    expired: list[dict] = []
    for e in entries:
        (expired if _is_expired(e) else active).append(e)

    # Auto-skip expired entries
    for e in expired:
        _log_queue_entry(e, status="skipped", notes="queue expired")
        logger.info(
            "Auto-skipped expired queue item: [%s] %s — %s",
            e.get("platform"), e.get("company_name"), e.get("role_title"),
        )

    if not active:
        _write_queue(QUEUE_PATH, [])
        skipped_msg = f"{len(expired)} expired item(s) auto-skipped." if expired else ""
        print(f"No active items in queue.{' ' + skipped_msg if skipped_msg else ''}")
        return

    print(f"\n{len(active)} item(s) in review queue"
          + (f" ({len(expired)} expired auto-skipped)" if expired else "") + ".")

    to_remove: set[int] = set()

    for idx, item in enumerate(active):
        _display_item(idx + 1, len(active), item)

        # Let user edit custom question answers (if any)
        raw_qs = item.get("custom_questions") or "[]"
        try:
            custom_qs = json.loads(raw_qs)
        except (json.JSONDecodeError, TypeError):
            custom_qs = []

        user_answers: dict[str, str] = {}
        if custom_qs:
            user_answers = _edit_answers(custom_qs)

        action = _prompt_action()

        if action == "q":
            print("\nQuitting — remaining items unchanged.")
            break

        if action == "a":
            print(f"\n  Launching browser for {item.get('platform')} apply…")
            asyncio.run(_queue_apply(item, user_answers, args))
            to_remove.add(idx)  # remove from queue regardless of apply outcome

        elif action == "s":
            _log_queue_entry(item, status="skipped", notes="reviewed: skipped")
            print("  Logged as skipped.")
            to_remove.add(idx)

        elif action == "d":
            url = item.get("job_url") or ""
            if url:
                webbrowser.open(url)
                print(f"  Opened {url} in browser.")
            else:
                print("  No URL — cannot open.")
            _log_queue_entry(item, status="skipped", notes="reviewed: manual")
            to_remove.add(idx)

        # "n" → leave in queue — don't add to to_remove

    # Rewrite queue keeping only items not processed (and expired entries already stripped)
    remaining = [e for i, e in enumerate(active) if i not in to_remove]
    _write_queue(QUEUE_PATH, remaining)

    processed = len(to_remove)
    print(f"\n  {processed} item(s) processed, {len(remaining)} remaining in queue.\n")


# ── Two-phase helpers (global cap flow) ───────────────────────────────


def _agent_queue_for_review(
    job: Job,
    fit_score: float,
    tier: str,
    reason: str,
    platform_name: str,
    log_path: Path,
) -> None:
    """Write a job to review_queue.csv when the relevance agent says 'queue'.

    Uses the same schema as platform _queue_for_review() methods.
    """
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=5)

    if not QUEUE_PATH.exists():
        QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(QUEUE_PATH, "w", newline="") as f:
            csv.writer(f).writerow(REVIEW_QUEUE_COLUMNS)

    row = {
        "queued_at": now.isoformat(),
        "platform": platform_name,
        "company_name": job.company,
        "role_title": job.title,
        "job_url": job.posted_date,
        "fit_score": f"{fit_score:.2f}",
        "tier": tier,
        "reason_queued": f"agent_review: {reason}",
        "custom_questions": "[]",
        "expires_at": expires.isoformat(),
    }
    with open(QUEUE_PATH, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=REVIEW_QUEUE_COLUMNS).writerow(row)


async def _discover_from_platform(
    name: str,
    platform,
    keywords: list[str],
    filters: dict,
    log_path: Path,
    agent: RelevanceAgent | None = None,
) -> tuple[list[Candidate], dict[str, int]]:
    """Phase 1: login, search all keywords, score/dedupe, collect candidates.

    Does NOT apply. Logs skipped jobs to CSV. The platform session is
    opened and closed within this function.

    Returns:
        (candidates, stats) where stats = {discovered, skipped, errored}.
    """
    candidates: list[Candidate] = []
    skipped = 0
    errored = 0

    logged_in = await platform.login()
    if not logged_in:
        logger.error("[%s] Login failed during discovery — skipping platform", name)
        return candidates, {"discovered": 0, "skipped": 0, "errored": 1}

    try:
        for keyword in keywords:
            # Respect platform-level kill switches (session time, STOP file)
            if hasattr(platform, '_should_stop') and platform._should_stop():
                break

            logger.info("[%s] Discovering: %s", name, keyword)
            try:
                async for job in platform.search(keyword, filters):
                    if hasattr(platform, '_should_stop') and platform._should_stop():
                        break

                    # 1. Dedupe (URL in job.posted_date)
                    if is_duplicate(name, job.posted_date, log_path):
                        skipped += 1
                        continue

                    # 2-4. Score, classify, threshold check
                    fit_score = score_job(job)
                    tier = classify_tier(job.title)
                    do_apply, reason = should_apply(job, fit_score, tier)

                    if not do_apply:
                        now = datetime.now(timezone.utc)
                        entry = LogEntry(
                            date_applied=now.strftime("%Y-%m-%d"),
                            time_applied=now.strftime("%H:%M"),
                            platform=name,
                            company_name=job.company,
                            role_title=job.title,
                            experience_required=job.experience_required,
                            location=job.location,
                            job_url=job.posted_date,
                            fit_score=fit_score,
                            status="skipped",
                            notes=reason,
                        )
                        log_application(entry, log_path)
                        skipped += 1
                        continue

                    # Relevance agent gate — semantic second-pass
                    if agent is not None:
                        verdict = agent.evaluate(job, name)
                        if verdict is None:
                            pass  # budget exceeded or unavailable — use keyword verdict
                        elif verdict.decision == "skip":
                            now = datetime.now(timezone.utc)
                            entry = LogEntry(
                                date_applied=now.strftime("%Y-%m-%d"),
                                time_applied=now.strftime("%H:%M"),
                                platform=name,
                                company_name=job.company,
                                role_title=job.title,
                                experience_required=job.experience_required,
                                location=job.location,
                                job_url=job.posted_date,
                                fit_score=fit_score,
                                status="skipped",
                                notes=f"agent skip: {verdict.reason}",
                            )
                            log_application(entry, log_path)
                            skipped += 1
                            continue
                        elif verdict.decision == "queue":
                            _agent_queue_for_review(job, fit_score, tier, verdict.reason, name, log_path)
                            now = datetime.now(timezone.utc)
                            entry = LogEntry(
                                date_applied=now.strftime("%Y-%m-%d"),
                                time_applied=now.strftime("%H:%M"),
                                platform=name,
                                company_name=job.company,
                                role_title=job.title,
                                experience_required=job.experience_required,
                                location=job.location,
                                job_url=job.posted_date,
                                fit_score=fit_score,
                                status="queued",
                                notes=f"agent queue: {verdict.reason}",
                            )
                            log_application(entry, log_path)
                            skipped += 1
                            continue
                        # verdict.decision == "apply" → proceed to candidate pool

                    candidates.append(Candidate(name, job, fit_score, tier))
            except Exception as exc:
                logger.error("[%s] Error during search for '%s': %s", name, keyword, exc, exc_info=True)
                errored += 1
    finally:
        try:
            await platform.logout()
        except Exception:
            pass

    logger.info("[%s] Discovery done: %d candidates, %d skipped, %d errors",
                name, len(candidates), skipped, errored)
    return candidates, {"discovered": len(candidates), "skipped": skipped, "errored": errored}


async def _apply_batch(
    name: str,
    candidates: list[Candidate],
    headless: bool,
    dry_run: bool,
    log_path: Path,
) -> dict[str, int]:
    """Phase 3: login, apply each allocated candidate, logout.

    Creates a fresh platform instance with its own browser context.

    Returns:
        Stats dict: {applied, skipped, errored, queued}.
    """
    import random

    try:
        cls = _load_platform_class(name)
    except Exception as exc:
        logger.error("[%s] Cannot load platform for apply batch: %s", name, exc)
        return {"applied": 0, "skipped": 0, "errored": len(candidates), "queued": 0}

    config = _build_config(name, headless=headless, dry_run=dry_run)
    platform = _create_platform(cls, config)

    logged_in = await platform.login()
    if not logged_in:
        logger.error("[%s] Login failed during apply phase — logging all as errors", name)
        now = datetime.now(timezone.utc)
        for c in candidates:
            entry = LogEntry(
                date_applied=now.strftime("%Y-%m-%d"),
                time_applied=now.strftime("%H:%M"),
                platform=name,
                company_name=c.job.company,
                role_title=c.job.title,
                experience_required=c.job.experience_required,
                location=c.job.location,
                job_url=c.job.posted_date,
                fit_score=c.fit_score,
                status="error",
                notes="login failed during apply phase",
            )
            log_application(entry, log_path)
        return {"applied": 0, "skipped": 0, "errored": len(candidates), "queued": 0}

    try:
        for candidate in candidates:
            if hasattr(platform, '_should_stop') and platform._should_stop():
                break

            # Check STOP file between applications
            if STOP_FILE.exists():
                logger.warning("STOP file detected during apply batch — stopping %s", name)
                break

            # Tier route — Yellow queue for borderline scores
            if hasattr(platform, '_should_queue') and platform._should_queue(candidate.fit_score, candidate.tier):
                if hasattr(platform, '_queue_for_review'):
                    platform._queue_for_review(
                        candidate.job, candidate.fit_score, candidate.tier, "score_in_review_band",
                    )
                continue

            # Dry run
            if dry_run:
                if hasattr(platform, '_record'):
                    platform._record(candidate.job, candidate.fit_score, "skipped", "dry run: would apply")
                continue

            # Apply using the platform's own attempt logic
            try:
                await platform._attempt_apply(candidate.job, candidate.fit_score, candidate.tier)
            except Exception as exc:
                logger.error("[%s] Error applying to %s: %s", name, candidate.job.posted_date, exc, exc_info=True)
                if hasattr(platform, '_record'):
                    platform._record(candidate.job, candidate.fit_score, "error", f"apply error: {exc}")

            # Delay between applications (5-15s, guidelines.md §3.2)
            delay = random.uniform(5, 15)
            logger.debug("Waiting %.1fs before next application", delay)
            await asyncio.sleep(delay)
    finally:
        try:
            await platform.logout()
        except Exception:
            pass

    return platform._stats.as_dict()


# ── Main async runner ──────────────────────────────────────────────────

async def _run_async(args: argparse.Namespace) -> None:
    """Async core: pre-flight → discover → rank → apply → post-run.

    Multi-platform mode uses the two-phase global-cap flow.
    Single-platform mode (--platform) uses the legacy per-platform flow
    for backward compatibility.
    """
    start_time = time.monotonic()

    # Determine which platforms to run
    platform_names = [args.platform] if args.platform else list(PLATFORM_ORDER)

    # Keywords
    if args.keyword:
        keywords = [args.keyword]
    else:
        keywords = PRIMARY_KEYWORDS + SECONDARY_KEYWORDS

    # Init log (idempotent — creates data/ if needed)
    init_log(LOG_PATH)

    # Pre-flight checks (guidelines.md §3.1)
    logger.info("Running pre-flight checks for: %s", ", ".join(platform_names))
    preflight_results = _preflight(platform_names, LOG_PATH)
    skipped_platforms = {k: v for k, v in preflight_results.items() if v is not None}
    run_platforms = [k for k, v in preflight_results.items() if v is None]

    if not run_platforms:
        logger.warning("All platforms skipped in pre-flight — nothing to do")
        _print_summary({}, time.monotonic() - start_time, skipped_platforms)
        return

    # Global kill switch: STOP file (guidelines.md §6)
    if STOP_FILE.exists():
        logger.warning("STOP file detected at %s — aborting before starting any platform", STOP_FILE)
        return

    headless = os.getenv("HEADLESS", "true").lower() != "false"

    # Resolve global cap
    global_cap = int(os.getenv("DAILY_CAP_GLOBAL", str(DAILY_CAP_GLOBAL_DEFAULT)))
    if args.cap is not None:
        logger.warning(
            "--cap override: global daily cap set to %d for this run (default: %d)",
            args.cap, global_cap,
        )
        global_cap = args.cap

    stats: dict[str, dict] = {}

    # ── Single-platform mode — legacy flow via platform.run() ─────────
    if args.platform:
        name = args.platform
        logger.info("[%s] Single-platform mode (dry_run=%s)", name, args.dry_run)

        try:
            cls = _load_platform_class(name)
        except Exception as exc:
            logger.error("[%s] Cannot load platform class: %s", name, exc)
            skipped_platforms[name] = f"skip: import error ({exc})"
            _print_summary({}, time.monotonic() - start_time, skipped_platforms)
            return

        config = _build_config(name, headless=headless, dry_run=args.dry_run)
        # In single-platform mode, cap is min(global, ceiling)
        config.daily_cap = min(global_cap, config.daily_cap)
        platform = _create_platform(cls, config)

        filters = {
            **FILTERS,
            "date_max_age_days": 14 if name in ("wellfound", "cutshort") else 7,
        }

        try:
            platform_stats = await platform.run(keywords, filters)
        except Exception as exc:
            logger.error("[%s] Unhandled exception during run: %s", name, exc, exc_info=True)
            platform_stats = {"applied": 0, "skipped": 0, "errored": 1, "queued": 0}

        stats[name] = platform_stats
        logger.info("[%s] Done: %s", name, platform_stats)

    # ── Multi-platform mode — two-phase global-cap flow ───────────────
    else:
        total_session_errors = 0

        # Relevance agent — semantic second-pass (disabled with --no-agent)
        agent: RelevanceAgent | None = None
        if not getattr(args, "no_agent", False):
            agent = RelevanceAgent()
            logger.info("Relevance agent enabled (model=%s, budget=%d/day)",
                        agent._model, agent._budget)
        else:
            logger.info("Relevance agent disabled (--no-agent)")

        # Phase 1: Discovery
        logger.info("Phase 1: Discovering candidates across %d platforms", len(run_platforms))
        all_candidates: list[Candidate] = []
        discovery_stats: dict[str, dict] = {}

        for name in run_platforms:
            if STOP_FILE.exists():
                logger.warning("STOP file detected — stopping discovery before %s", name)
                break
            if total_session_errors >= MAX_TOTAL_ERRORS:
                logger.error("Total errors (%d) exceeded limit — stopping discovery", total_session_errors)
                break

            try:
                cls = _load_platform_class(name)
            except Exception as exc:
                logger.error("[%s] Cannot load platform class: %s", name, exc)
                skipped_platforms[name] = f"skip: import error ({exc})"
                continue

            config = _build_config(name, headless=headless, dry_run=args.dry_run)
            platform = _create_platform(cls, config)

            filters = {
                **FILTERS,
                "date_max_age_days": 14 if name in ("wellfound", "cutshort") else 7,
            }

            candidates, disc_stats = await _discover_from_platform(
                name, platform, keywords, filters, LOG_PATH, agent=agent,
            )
            all_candidates.extend(candidates)
            discovery_stats[name] = disc_stats
            total_session_errors += disc_stats.get("errored", 0)
            logger.info("[%s] Discovery: %s", name, disc_stats)

        logger.info(
            "Discovery complete: %d candidates from %d platform(s)",
            len(all_candidates), len(run_platforms) - len(skipped_platforms),
        )

        # Phase 2: Rank and allocate
        ceilings = {
            name: int(os.getenv(
                f"DAILY_CAP_{name.upper()}_CEILING",
                str(DAILY_CEILINGS_DEFAULT.get(name, 100)),
            ))
            for name in run_platforms
        }
        already_applied = count_today_all(run_platforms, LOG_PATH)

        allocated, overflow = rank_and_allocate(
            all_candidates, global_cap, ceilings, already_applied,
        )
        logger.info(
            "Allocation: %d to apply, %d overflow (global cap %d, ceilings %s)",
            len(allocated), len(overflow), global_cap, ceilings,
        )

        # Log overflow candidates as skipped
        if overflow:
            now = datetime.now(timezone.utc)
            for c in overflow:
                entry = LogEntry(
                    date_applied=now.strftime("%Y-%m-%d"),
                    time_applied=now.strftime("%H:%M"),
                    platform=c.platform,
                    company_name=c.job.company,
                    role_title=c.job.title,
                    experience_required=c.job.experience_required,
                    location=c.job.location,
                    job_url=c.job.posted_date,
                    fit_score=c.fit_score,
                    status="skipped",
                    notes=f"overflow: below global cap cutoff (score {c.fit_score:.2f})",
                )
                log_application(entry, LOG_PATH)

        # Phase 3: Apply (grouped by platform)
        by_platform: dict[str, list[Candidate]] = {}
        for c in allocated:
            by_platform.setdefault(c.platform, []).append(c)

        for name in run_platforms:
            batch = by_platform.get(name, [])
            if not batch:
                # Carry discovery stats even if nothing allocated
                stats[name] = discovery_stats.get(name, {})
                continue

            if STOP_FILE.exists():
                logger.warning("STOP file detected — stopping apply before %s", name)
                break
            if total_session_errors >= MAX_TOTAL_ERRORS:
                logger.error("Total errors (%d) exceeded limit — stopping apply", total_session_errors)
                break

            logger.info("[%s] Phase 3: Applying to %d candidates (dry_run=%s)", name, len(batch), args.dry_run)
            batch_stats = await _apply_batch(name, batch, headless, args.dry_run, LOG_PATH)

            # Merge discovery + apply stats
            merged = dict(discovery_stats.get(name, {}))
            merged.update(batch_stats)
            merged["discovered"] = discovery_stats.get(name, {}).get("discovered", 0)
            stats[name] = merged
            total_session_errors += batch_stats.get("errored", 0)
            logger.info("[%s] Apply done: %s", name, batch_stats)

    runtime = time.monotonic() - start_time

    # Post-run reporting (guidelines.md §3.7)
    _write_summary(stats, runtime, SUMMARY_PATH)
    _print_summary(stats, runtime, skipped_platforms)

    if getattr(args, "email_summary", False):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        send_digest(
            date=today,
            log_path=LOG_PATH,
            summary_path=SUMMARY_PATH,
            queue_path=QUEUE_PATH,
            smtp_config=build_smtp_config(),
            print_only=getattr(args, "dry_email", False),
        )


# ── CLI ────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """Parse CLI arguments. See CLAUDE.md §10."""
    parser = argparse.ArgumentParser(
        description="job-bot: auto-apply to relevant jobs across Indian job boards",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python apply.py --dry-run                Score and log only\n"
            "  python apply.py --platform naukri         Run Naukri only\n"
            '  python apply.py --keyword "founding"      Override keywords\n'
            "  python apply.py --review-queue            Review Yellow queue\n"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Score and log but never click Apply",
    )
    parser.add_argument(
        "--platform",
        type=str,
        choices=list(PLATFORM_ORDER),
        help="Run only this platform",
    )
    parser.add_argument(
        "--keyword",
        type=str,
        help="Override search keywords with a single keyword",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        help="Override apply threshold for this run (not yet wired into scorer)",
    )
    parser.add_argument(
        "--cap",
        type=int,
        default=None,
        help=(
            "Override global daily cap (DAILY_CAP_GLOBAL) for this run only. "
            "Per-platform ceilings still apply. "
            "CLI-only — never persisted. Logs a WARNING when used."
        ),
    )
    parser.add_argument(
        "--email-summary-only",
        action="store_true",
        help="Email yesterday's summary only; don't run the bot",
    )
    parser.add_argument(
        "--email-summary",
        action="store_true",
        help="Send the digest email after the run completes",
    )
    parser.add_argument(
        "--dry-email",
        action="store_true",
        help="Print the email body to stdout instead of sending (safe to run before adding SMTP creds)",
    )
    parser.add_argument(
        "--review-queue",
        action="store_true",
        help="Interactive review of Yellow-tier queue",
    )
    parser.add_argument(
        "--no-agent",
        action="store_true",
        help="Disable the LLM relevance agent (use keyword scorer only)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Set log level to DEBUG",
    )
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    """Entry point — validates early-exit modes, then runs the async pipeline."""
    if args.review_queue:
        _handle_review_queue(args)
        return

    if args.email_summary_only:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        send_digest(
            date=yesterday,
            log_path=LOG_PATH,
            summary_path=SUMMARY_PATH,
            queue_path=QUEUE_PATH,
            smtp_config=build_smtp_config(),
            print_only=args.dry_email,
        )
        return

    asyncio.run(_run_async(args))


def main() -> None:
    """Configure logging and dispatch."""
    load_dotenv()

    args = parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    # Silence noisy libraries at INFO level
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    run(args)


if __name__ == "__main__":
    main()
