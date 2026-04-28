#!/usr/bin/env bash
# scripts/run_daily.sh — daily job-bot runner
#
# Called by cron or launchd. Finds the project root, activates .venv if
# present, creates a dated log file, then runs the full apply pipeline.
# Exit code mirrors python apply.py so cron/launchd can detect failures.

set -euo pipefail

# Resolve project root from the script's own location (symlink-safe)
BOTDIR="$(cd "$(dirname "$0")/.." && pwd)"
LOGDIR="$BOTDIR/data/logs"
LOG="$LOGDIR/$(date +%Y-%m-%d).log"

mkdir -p "$LOGDIR"
cd "$BOTDIR"

# Activate virtualenv at the conventional .venv location if it exists.
# Skip silently if the project runs against the system Python.
if [ -f ".venv/bin/activate" ]; then
    # shellcheck source=/dev/null
    source ".venv/bin/activate"
fi

python apply.py --email-summary >> "$LOG" 2>&1
