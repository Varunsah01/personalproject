#!/usr/bin/env bash
# smoke_outreach.sh — quick sanity check for the outreach module.
# Usage: bash scripts/smoke_outreach.sh
# Exits non-zero if any check fails.

set -euo pipefail

FAILURES=0
BOTDIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BOTDIR"

run() {
    echo ""
    echo "==> $*"
    if "$@"; then
        echo "    OK"
    else
        echo "    FAIL: exit $?"
        FAILURES=$((FAILURES + 1))
    fi
}

echo "========================================"
echo "  Outreach smoke checks"
echo "  $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "========================================"

# 1. Pipeline unit tests
run python3 -m pytest tests/test_outreach_pipeline.py -v

# 2. Dashboard smoke (read-only, must not crash)
run python3 outreach/dashboard.py

# 3. Pipeline dry-run (real subagent invocation; requires claude on PATH)
run python3 outreach/pipeline.py --stage all --dry-run

echo ""
echo "========================================"
if [ "$FAILURES" -eq 0 ]; then
    echo "  All checks passed."
    exit 0
else
    echo "  $FAILURES check(s) FAILED."
    exit 1
fi
