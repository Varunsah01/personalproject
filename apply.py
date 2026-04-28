#!/usr/bin/env python3
"""Main runner for job-bot.

Orchestrates the full daily pipeline: pre-flight checks, sequential
platform runs, post-run summary. See CLAUDE.md §6 and §10 for the
daily routine and CLI flags.
"""

from __future__ import annotations

import argparse
import sys


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


def run(args: argparse.Namespace) -> None:
    """Execute the daily pipeline. Not yet implemented."""
    print("not implemented")


def main() -> None:
    """Entry point."""
    args = parse_args()
    run(args)


if __name__ == "__main__":
    main()
