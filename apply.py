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

from core.logger import count_today, init_log, read_log
from core.notifier import build_smtp_config, send_digest

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
PLATFORM_ORDER: list[str] = ["naukri", "linkedin", "wellfound", "cutshort"]

# Defaults; overridden by DAILY_CAP_<PLATFORM> in .env
DAILY_CAPS: dict[str, int] = {
    "naukri": 75,
    "linkedin": 40,
    "wellfound": 30,
    "cutshort": 25,
}

# Env var pairs (email_key, password_key) per platform
CREDENTIAL_KEYS: dict[str, tuple[str, str]] = {
    "naukri": ("NAUKRI_EMAIL", "NAUKRI_PASSWORD"),
    "linkedin": ("LINKEDIN_EMAIL", "LINKEDIN_PASSWORD"),
    "wellfound": ("WELLFOUND_EMAIL", "WELLFOUND_PASSWORD"),
    "cutshort": ("CUTSHORT_EMAIL", "CUTSHORT_PASSWORD"),
}


# ── Paths and thresholds ───────────────────────────────────────────────

LOG_PATH = Path(os.getenv("LOG_FILE", "data/applications_log.csv"))
SUMMARY_PATH = Path(os.getenv("SUMMARY_FILE", "data/daily_summary.csv"))
RESUME_PATH = Path("Varun_Sah_CV.pdf")
STOP_FILE = Path("data/STOP")
QUEUE_PATH = Path("data/review_queue.csv")

MIN_FREE_DISK_MB = 500          # guidelines.md §6
RESUME_MAX_AGE_DAYS = 60        # guidelines.md §3.1
MAX_TOTAL_ERRORS = 50           # guidelines.md §6
ERROR_RATE_THRESHOLD = 0.25     # guidelines.md §3.1 + §6

SUMMARY_COLUMNS = [
    "date", "total_applied",
    "naukri_applied", "linkedin_applied", "wellfound_applied", "cutshort_applied",
    "total_skipped", "total_errors", "runtime_seconds",
]


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

        # 3. Already at daily cap from a previous run today?
        cap = int(os.getenv(f"DAILY_CAP_{name.upper()}", str(DAILY_CAPS.get(name, 999))))
        today_count = count_today(name, log_path)
        if today_count >= cap:
            reason = f"skip: already at daily cap ({today_count}/{cap})"
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

    row = {
        "date": today,
        "total_applied": sum(_applied(n) for n in PLATFORM_ORDER),
        "naukri_applied": _applied("naukri"),
        "linkedin_applied": _applied("linkedin"),
        "wellfound_applied": _applied("wellfound"),
        "cutshort_applied": _applied("cutshort"),
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
    print(f"  {'Platform':<12} {'Applied':>7} {'Skipped':>8} {'Errors':>7} {'Queued':>7}")
    print("─" * width)

    total_applied = total_skipped = total_errors = 0

    for name in PLATFORM_ORDER:
        if name in skipped_platforms:
            reason = skipped_platforms[name].replace("skip: ", "")
            print(f"  {name:<12} {'—':>7} {'—':>8} {'—':>7} {'—':>7}  ({reason})")
            continue
        if name not in stats:
            continue
        s = stats[name]
        applied = s.get("applied", 0) + s.get("applied_unconfirmed", 0)
        skipped = s.get("skipped", 0)
        errors = s.get("errored", 0)
        queued = s.get("queued", 0)
        print(f"  {name:<12} {applied:>7} {skipped:>8} {errors:>7} {queued:>7}")
        total_applied += applied
        total_skipped += skipped
        total_errors += errors

    print("─" * width)
    print(f"  {'TOTAL':<12} {total_applied:>7} {total_skipped:>8} {total_errors:>7}")
    print(f"\n  Runtime: {runtime_seconds:.0f}s")
    print("─" * width)
    print()


# ── Main async runner ──────────────────────────────────────────────────

async def _run_async(args: argparse.Namespace) -> None:
    """Async core: pre-flight → platform runs → post-run."""
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

    stats: dict[str, dict] = {}
    total_session_errors = 0

    for name in run_platforms:
        # Kill switch checks before each platform (guidelines.md §6)
        if STOP_FILE.exists():
            logger.warning("STOP file detected — stopping before %s", name)
            break

        if total_session_errors >= MAX_TOTAL_ERRORS:
            logger.error(
                "Total session errors (%d) exceeded limit (%d) — aborting run",
                total_session_errors, MAX_TOTAL_ERRORS,
            )
            break

        logger.info("[%s] Starting platform run (dry_run=%s)", name, args.dry_run)

        # Load platform class from registry
        try:
            cls = _load_platform_class(name)
        except Exception as exc:
            logger.error("[%s] Cannot load platform class: %s", name, exc)
            skipped_platforms[name] = f"skip: import error ({exc})"
            continue

        headless = os.getenv("HEADLESS", "true").lower() != "false"
        cap = int(os.getenv(f"DAILY_CAP_{name.upper()}", str(DAILY_CAPS.get(name, 999))))

        platform = cls(
            log_path=LOG_PATH,
            headless=headless,
            dry_run=args.dry_run,
        )

        if args.cap is not None:
            logger.warning(
                "[%s] --cap override: daily cap set to %d for this run only (normal cap: %d)",
                name, args.cap, platform.daily_cap,
            )
            platform.daily_cap = args.cap

        # Build per-platform filters (Wellfound/Cutshort get 14-day window)
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
        total_session_errors += platform_stats.get("errored", 0)
        logger.info("[%s] Done: %s", name, platform_stats)

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
            "Override daily cap for this run only. "
            "CLI-only — never read from env, never persisted. "
            "Logs a WARNING when used."
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
        "--verbose",
        action="store_true",
        help="Set log level to DEBUG",
    )
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    """Entry point — validates early-exit modes, then runs the async pipeline."""
    # Review queue: stub until build step 9
    if args.review_queue:
        print("Review queue handler not implemented yet.")
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
