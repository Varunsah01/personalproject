#!/usr/bin/env python3
"""Main runner for job-bot.

Orchestrates the full daily pipeline: pre-flight checks, sequential
platform runs, post-run summary. See CLAUDE.md §6 and §10 for the
daily routine and CLI flags.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from core.logger import ApplicationLogger
from core.scorer import JobScorer

logger = logging.getLogger("job-bot")

# Platform registry — maps CLI names to (class, env prefix, default cap)
PLATFORM_REGISTRY: dict[str, tuple[type, str, int]] = {}

# Search keywords from CLAUDE.md §4
PRIMARY_KEYWORDS = [
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

SECONDARY_KEYWORDS = [
    "partnerships manager",
    "revenue operations",
    "VC analyst",
    "chief of staff",
    "program manager",
    "associate product manager",
]

NICHE_KEYWORDS = [
    "founding growth",
    "founder's office",
    "growth associate",
    "early-stage operator",
    "0 to 1",
    "pre-seed analyst",
]

# Default filters from CLAUDE.md §5
DEFAULT_FILTERS = {
    "locations": [
        "Delhi NCR", "Delhi", "Gurgaon", "Noida",
        "Bangalore", "Bengaluru", "Mumbai",
        "Remote India", "Pan India",
    ],
    "experience_min": 1,
    "experience_max": 5,
}

# Resume file (profile.md §19)
RESUME_PATH = Path("Varun_Sah_CV.pdf")
RESUME_MAX_AGE_DAYS = 60


def _register_platforms() -> None:
    """Import and register platform classes.

    Deferred import so missing platform implementations don't crash
    the CLI when only using --help or --email-summary-only.
    """
    # Only import what's actually implemented. Stubs will raise
    # NotImplementedError at runtime, which is fine — the import
    # itself should work.
    try:
        from platforms.naukri import NaukriPlatform
        PLATFORM_REGISTRY["naukri"] = (NaukriPlatform, "NAUKRI", 75)
    except ImportError:
        logger.warning("Naukri platform module not found")

    try:
        from platforms.linkedin import LinkedInPlatform
        PLATFORM_REGISTRY["linkedin"] = (LinkedInPlatform, "LINKEDIN", 40)
    except ImportError:
        logger.warning("LinkedIn platform module not found")

    try:
        from platforms.wellfound import WellfoundPlatform
        PLATFORM_REGISTRY["wellfound"] = (WellfoundPlatform, "WELLFOUND", 30)
    except ImportError:
        logger.warning("Wellfound platform module not found")

    try:
        from platforms.cutshort import CutshortPlatform
        PLATFORM_REGISTRY["cutshort"] = (CutshortPlatform, "CUTSHORT", 25)
    except ImportError:
        logger.warning("Cutshort platform module not found")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments. See CLAUDE.md §10."""
    parser = argparse.ArgumentParser(
        description="job-bot: auto-apply to relevant jobs across Indian job boards",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python apply.py --dry-run              Score and log only\n"
            "  python apply.py --platform naukri       Run Naukri only\n"
            '  python apply.py --keyword "founding"    Override keywords\n'
            "  python apply.py --review-queue          Review Yellow queue\n"
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
        choices=["naukri", "linkedin", "wellfound", "cutshort"],
        help="Run only this platform",
    )
    parser.add_argument(
        "--keyword",
        type=str,
        help="Override search keywords (single keyword)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        help="Override apply threshold for this run",
    )
    parser.add_argument(
        "--email-summary",
        action="store_true",
        help="Send digest email at end of run",
    )
    parser.add_argument(
        "--email-summary-only",
        action="store_true",
        help="Just email yesterday's summary, don't run the bot",
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


def setup_logging(verbose: bool = False) -> None:
    """Configure stdlib logging. INFO by default, DEBUG with --verbose."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def preflight_checks(app_logger: ApplicationLogger, platforms: list[str]) -> list[str]:
    """Run pre-flight checks from guidelines.md §3.1.

    Returns:
        List of platform names that passed checks and should be run.
    """
    runnable: list[str] = []

    # Check STOP file (guidelines.md §6)
    if Path("data/STOP").exists():
        logger.error("STOP file found at data/STOP — aborting entire run")
        sys.exit(1)

    # Check resume file
    if not RESUME_PATH.exists():
        logger.error("Resume file not found at %s", RESUME_PATH)
        sys.exit(1)

    resume_age_days = (
        datetime.now(timezone.utc)
        - datetime.fromtimestamp(RESUME_PATH.stat().st_mtime, tz=timezone.utc)
    ).days
    if resume_age_days > RESUME_MAX_AGE_DAYS:
        logger.warning(
            "Resume is %d days old (> %d) — consider updating",
            resume_age_days,
            RESUME_MAX_AGE_DAYS,
        )

    for platform_name in platforms:
        if platform_name not in PLATFORM_REGISTRY:
            logger.warning("Platform '%s' not registered — skipping", platform_name)
            continue

        _, env_prefix, default_cap = PLATFORM_REGISTRY[platform_name]

        # Validate credentials
        email = os.getenv(f"{env_prefix}_EMAIL")
        password = os.getenv(f"{env_prefix}_PASSWORD")
        if not email or not password:
            logger.warning(
                "Missing credentials for %s (%s_EMAIL / %s_PASSWORD) — skipping",
                platform_name,
                env_prefix,
                env_prefix,
            )
            continue

        # Check daily cap
        cap = int(os.getenv(f"DAILY_CAP_{env_prefix}", str(default_cap)))
        today_counts = app_logger.get_today_counts()
        current = today_counts.get(platform_name, 0)
        if current >= cap:
            logger.info(
                "%s already at daily cap (%d/%d) — skipping",
                platform_name,
                current,
                cap,
            )
            continue

        # Check error rate (guidelines.md §3.1 step 4)
        error_rate = app_logger.get_recent_error_rate(platform_name)
        if error_rate > 0.25:
            logger.warning(
                "%s error rate %.0f%% in last 24h (> 25%%) — skipping and notifying",
                platform_name,
                error_rate * 100,
            )
            continue

        runnable.append(platform_name)

    return runnable


async def run_platforms(
    platforms: list[str],
    app_logger: ApplicationLogger,
    scorer: JobScorer,
    keywords: list[str],
    filters: dict,
    dry_run: bool,
) -> dict[str, dict[str, int]]:
    """Run each platform sequentially. Returns per-platform stats."""
    all_stats: dict[str, dict[str, int]] = {}

    for platform_name in platforms:
        platform_cls, env_prefix, default_cap = PLATFORM_REGISTRY[platform_name]
        cap = int(os.getenv(f"DAILY_CAP_{env_prefix}", str(default_cap)))

        # Build standard answers from .env and profile data
        standard_answers = {
            "email": os.getenv(f"{env_prefix}_EMAIL", ""),
            "name": "Varun Sah",
            "phone": "+91-8595062552",
            "location": "Delhi NCR",
            "notice_period": "Immediate",
            "linkedin": "https://www.linkedin.com/in/varun-sah/",
        }

        platform = platform_cls(
            platform_name=platform_name,
            app_logger=app_logger,
            scorer=scorer,
            daily_cap=cap,
            dry_run=dry_run,
            standard_answers=standard_answers,
        )

        logger.info("=== Starting %s ===", platform_name)
        try:
            stats = await platform.run(keywords, filters)
        except Exception:
            logger.exception("Platform %s crashed", platform_name)
            stats = {"applied": 0, "skipped": 0, "errored": 1, "queued": 0}
        all_stats[platform_name] = stats

    return all_stats


def print_summary(all_stats: dict[str, dict[str, int]], runtime_seconds: float) -> None:
    """Print the post-run summary table to stdout."""
    print("\n" + "=" * 60)
    print(f"{'Platform':<15} {'Applied':>8} {'Skipped':>8} {'Errored':>8} {'Queued':>8}")
    print("-" * 60)

    totals = {"applied": 0, "skipped": 0, "errored": 0, "queued": 0}
    for platform, stats in all_stats.items():
        print(
            f"{platform:<15} {stats.get('applied', 0):>8} "
            f"{stats.get('skipped', 0):>8} {stats.get('errored', 0):>8} "
            f"{stats.get('queued', 0):>8}"
        )
        for key in totals:
            totals[key] += stats.get(key, 0)

    print("-" * 60)
    print(
        f"{'TOTAL':<15} {totals['applied']:>8} {totals['skipped']:>8} "
        f"{totals['errored']:>8} {totals['queued']:>8}"
    )
    print(f"\nRuntime: {runtime_seconds:.0f}s")
    print("=" * 60 + "\n")


async def main() -> None:
    """Main entry point."""
    args = parse_args()
    setup_logging(verbose=args.verbose)

    # Load .env
    load_dotenv()

    # Handle --email-summary-only
    if args.email_summary_only:
        logger.info("Email-only mode — sending yesterday's digest")
        from core.notifier import send_daily_digest
        # This will raise NotImplementedError until step 9
        await send_daily_digest({}, {})
        return

    # Handle --review-queue
    if args.review_queue:
        logger.info("Review queue mode — not yet implemented (build step 6)")
        print("Review queue is not yet implemented. Coming in build step 6.")
        return

    # Register platforms
    _register_platforms()

    # Initialize logger and scorer
    log_file = os.getenv("LOG_FILE", "data/applications_log.csv")
    summary_file = os.getenv("SUMMARY_FILE", "data/daily_summary.csv")
    app_logger = ApplicationLogger(
        log_file=log_file,
        summary_file=summary_file,
    )
    scorer = JobScorer()

    # Override threshold if specified
    if args.threshold is not None:
        scorer.thresholds = {
            tier: args.threshold for tier in scorer.thresholds
        }
        logger.info("Threshold overridden to %.2f for all tiers", args.threshold)

    # Determine which platforms to run
    if args.platform:
        requested_platforms = [args.platform]
    else:
        requested_platforms = ["naukri", "linkedin", "wellfound", "cutshort"]

    # Determine keywords
    if args.keyword:
        keywords = [args.keyword]
    else:
        keywords = PRIMARY_KEYWORDS + SECONDARY_KEYWORDS + NICHE_KEYWORDS

    # Pre-flight
    runnable = preflight_checks(app_logger, requested_platforms)
    if not runnable:
        logger.warning("No platforms passed pre-flight checks. Nothing to run.")
        return

    logger.info("Platforms to run: %s", runnable)
    logger.info("Keywords: %d total", len(keywords))
    logger.info("Dry run: %s", args.dry_run)

    # Run
    start_time = time.monotonic()
    all_stats = await run_platforms(
        platforms=runnable,
        app_logger=app_logger,
        scorer=scorer,
        keywords=keywords,
        filters=DEFAULT_FILTERS,
        dry_run=args.dry_run,
    )
    runtime = time.monotonic() - start_time

    # Post-run
    print_summary(all_stats, runtime)

    # Write daily summary
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary_row = {
        "date": today,
        "total_applied": sum(s.get("applied", 0) for s in all_stats.values()),
        "naukri_applied": all_stats.get("naukri", {}).get("applied", 0),
        "linkedin_applied": all_stats.get("linkedin", {}).get("applied", 0),
        "wellfound_applied": all_stats.get("wellfound", {}).get("applied", 0),
        "cutshort_applied": all_stats.get("cutshort", {}).get("applied", 0),
        "total_skipped": sum(s.get("skipped", 0) for s in all_stats.values()),
        "total_errors": sum(s.get("errored", 0) for s in all_stats.values()),
        "runtime_seconds": int(runtime),
    }
    app_logger.write_daily_summary(summary_row)

    # Email summary if requested
    if args.email_summary:
        logger.info("Sending digest email...")
        from core.notifier import send_daily_digest
        try:
            await send_daily_digest(summary_row, {
                "SMTP_HOST": os.getenv("SMTP_HOST", ""),
                "SMTP_PORT": os.getenv("SMTP_PORT", "587"),
                "SMTP_USER": os.getenv("SMTP_USER", ""),
                "SMTP_PASSWORD": os.getenv("SMTP_PASSWORD", ""),
                "DIGEST_TO": os.getenv("DIGEST_TO", "varunsah@yahoo.com"),
            })
        except NotImplementedError:
            logger.warning("Notifier not yet implemented — skipping email")


if __name__ == "__main__":
    asyncio.run(main())
