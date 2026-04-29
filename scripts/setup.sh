#!/usr/bin/env bash
# scripts/setup.sh — First-run setup wizard for job-bot + outreach pipeline.
# Checks dependencies, creates directories, copies env files, validates config,
# runs OAuth setup for unconfigured inboxes, seeds tracker, and smoke-tests.
#
# Exit 0 only if every step passes.

set -uo pipefail

# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

GREEN="\033[0;32m"
RED="\033[0;31m"
YELLOW="\033[0;33m"
DIM="\033[2m"
BOLD="\033[1m"
RESET="\033[0m"

pass() { printf "  ${GREEN}✓${RESET} %s\n" "$1"; }
fail() { printf "  ${RED}✗${RESET} %s\n    ${DIM}→ %s${RESET}\n" "$1" "$2"; FAILED=1; }
warn() { printf "  ${YELLOW}!${RESET} %s\n" "$1"; }
header() { printf "\n${BOLD}%s${RESET}\n" "$1"; }

FAILED=0

# ---------------------------------------------------------------------------
# Resolve project root (parent of scripts/)
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

printf "${BOLD}job-bot setup wizard${RESET}  ${DIM}$(date +%Y-%m-%d)${RESET}\n"
printf "${DIM}project root: %s${RESET}\n" "$PROJECT_ROOT"

# ---------------------------------------------------------------------------
# Step 1 — Dependencies
# ---------------------------------------------------------------------------

header "1. Dependencies"

# Python >= 3.11
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 11 ]; then
        pass "Python $PY_VERSION"
    else
        fail "Python $PY_VERSION (need >= 3.11)" "brew install python@3.12 or pyenv install 3.12"
    fi
else
    fail "python3 not found" "install Python 3.11+ from python.org or brew"
fi

# Node >= 20
if command -v node &>/dev/null; then
    NODE_VERSION=$(node -v | sed 's/v//')
    NODE_MAJOR=$(echo "$NODE_VERSION" | cut -d. -f1)
    if [ "$NODE_MAJOR" -ge 20 ]; then
        pass "Node $NODE_VERSION"
    else
        fail "Node $NODE_VERSION (need >= 20)" "nvm install 20 or brew install node@20"
    fi
else
    fail "node not found" "install Node.js 20+ from nodejs.org or nvm"
fi

# npm
if command -v npm &>/dev/null; then
    NPM_VERSION=$(npm -v)
    pass "npm $NPM_VERSION"
else
    fail "npm not found" "comes with Node.js — reinstall Node"
fi

# ---------------------------------------------------------------------------
# Step 2 — Directories
# ---------------------------------------------------------------------------

header "2. Directories"

DIRS=(
    "secrets"
    "outreach/data/drafts"
    "outreach/data/sent"
    "data/logs"
)

for dir in "${DIRS[@]}"; do
    if [ -d "$dir" ]; then
        pass "$dir/ exists"
    else
        mkdir -p "$dir"
        pass "$dir/ created"
    fi
done

# ---------------------------------------------------------------------------
# Step 3 — .env file
# ---------------------------------------------------------------------------

header "3. Environment files"

if [ -f ".env" ]; then
    pass ".env exists"
else
    if [ -f ".env.example" ]; then
        cp .env.example .env
        pass ".env created from .env.example — fill in your credentials"
    else
        fail ".env missing and no .env.example found" "create .env manually"
    fi
fi

# ---------------------------------------------------------------------------
# Step 4 — .env.outreach file
# ---------------------------------------------------------------------------

if [ -f ".env.outreach" ]; then
    pass ".env.outreach exists"
else
    if [ -f ".env.outreach.example" ]; then
        cp .env.outreach.example .env.outreach
        pass ".env.outreach created from .env.outreach.example — fill in inbox config"
    else
        fail ".env.outreach missing and no .env.outreach.example found" "create .env.outreach manually"
    fi
fi

# ---------------------------------------------------------------------------
# Step 5 — Validate .env.outreach inbox configuration
# ---------------------------------------------------------------------------

header "4. Inbox configuration"

INBOX_COUNT=0
EMPTY_FIELDS=()

_env_val() {
    # Extract value from KEY=VALUE line, stripping inline comments and whitespace
    local key="$1" file="$2"
    grep "^${key}=" "$file" 2>/dev/null | cut -d= -f2- | sed 's/#.*//' | xargs 2>/dev/null || true
}

if [ -f ".env.outreach" ]; then
    for i in 1 2 3 4 5; do
        ADDR_KEY="OUTREACH_INBOX_${i}_ADDRESS"
        TOKEN_KEY="OUTREACH_INBOX_${i}_TOKEN_PATH"

        ADDR_VAL=$(_env_val "$ADDR_KEY" .env.outreach)
        TOKEN_VAL=$(_env_val "$TOKEN_KEY" .env.outreach)

        if [ -n "$ADDR_VAL" ] && [ -n "$TOKEN_VAL" ]; then
            INBOX_COUNT=$((INBOX_COUNT + 1))
            pass "Inbox $i: $ADDR_VAL"
        elif [ -n "$ADDR_VAL" ] && [ -z "$TOKEN_VAL" ]; then
            EMPTY_FIELDS+=("$TOKEN_KEY")
            warn "Inbox $i: address set ($ADDR_VAL) but TOKEN_PATH empty"
        elif [ -z "$ADDR_VAL" ] && [ -n "$TOKEN_VAL" ]; then
            EMPTY_FIELDS+=("$ADDR_KEY")
            warn "Inbox $i: TOKEN_PATH set but ADDRESS empty"
        fi
    done

    if [ "$INBOX_COUNT" -ge 1 ]; then
        pass "$INBOX_COUNT inbox(es) fully configured"
    else
        fail "No inboxes configured in .env.outreach" "set OUTREACH_INBOX_1_ADDRESS and _TOKEN_PATH"
    fi

    if [ ${#EMPTY_FIELDS[@]} -gt 0 ]; then
        warn "Empty fields: ${EMPTY_FIELDS[*]}"
    fi
else
    fail ".env.outreach not found" "run this script again after creating the file"
fi

# ---------------------------------------------------------------------------
# Step 6 — Gmail OAuth tokens
# ---------------------------------------------------------------------------

header "5. Gmail OAuth tokens"

if [ "$INBOX_COUNT" -ge 1 ] && [ -f ".env.outreach" ]; then
    OAUTH_NEEDED=0
    for i in 1 2 3 4 5; do
        ADDR_VAL=$(_env_val "OUTREACH_INBOX_${i}_ADDRESS" .env.outreach)
        TOKEN_VAL=$(_env_val "OUTREACH_INBOX_${i}_TOKEN_PATH" .env.outreach)

        if [ -n "$ADDR_VAL" ] && [ -n "$TOKEN_VAL" ]; then
            if [ -f "$TOKEN_VAL" ]; then
                pass "$ADDR_VAL — token exists at $TOKEN_VAL"
            else
                warn "$ADDR_VAL — token missing at $TOKEN_VAL"
                OAUTH_NEEDED=1

                # Check if setup script and client_secret exist
                if [ -f "outreach/lib/setup_gmail_oauth.py" ]; then
                    printf "    ${DIM}Run OAuth setup for this inbox? (opens browser) [y/N]${RESET} "
                    read -r REPLY
                    if [[ "$REPLY" =~ ^[Yy]$ ]]; then
                        printf "    ${DIM}Path to client_secret.json:${RESET} "
                        read -r CLIENT_SECRET
                        if [ -f "$CLIENT_SECRET" ]; then
                            python3 outreach/lib/setup_gmail_oauth.py \
                                --client-secret "$CLIENT_SECRET" \
                                --inbox-address "$ADDR_VAL" && \
                                pass "OAuth token created for $ADDR_VAL" || \
                                fail "OAuth setup failed for $ADDR_VAL" "check console output above"
                        else
                            fail "client_secret.json not found at $CLIENT_SECRET" "download from Google Cloud Console"
                        fi
                    else
                        warn "Skipped OAuth for $ADDR_VAL — run manually later"
                    fi
                else
                    fail "setup_gmail_oauth.py not found" "expected at outreach/lib/setup_gmail_oauth.py"
                fi
            fi
        fi
    done

    if [ "$OAUTH_NEEDED" -eq 0 ]; then
        pass "All configured inboxes have OAuth tokens"
    fi
else
    warn "No inboxes configured — skipping OAuth check"
fi

# ---------------------------------------------------------------------------
# Step 7 — Seed tracker.csv
# ---------------------------------------------------------------------------

header "6. Tracker CSV"

if [ -f "outreach/data/tracker.csv" ]; then
    ROW_COUNT=$(wc -l < "outreach/data/tracker.csv" | xargs)
    pass "tracker.csv exists ($ROW_COUNT lines)"
else
    if python3 -c "from outreach.lib.tracker import init_tracker; init_tracker()" 2>/dev/null; then
        pass "tracker.csv created with header row"
    else
        fail "Could not seed tracker.csv" "run: python3 -c \"from outreach.lib.tracker import init_tracker; init_tracker()\""
    fi
fi

# ---------------------------------------------------------------------------
# Step 8 — Smoke test
# ---------------------------------------------------------------------------

header "7. Smoke test"

if [ -f "scripts/smoke_outreach.sh" ]; then
    if bash scripts/smoke_outreach.sh >/dev/null 2>&1; then
        pass "smoke_outreach.sh passed"
    else
        fail "smoke_outreach.sh failed" "run 'bash scripts/smoke_outreach.sh' for details"
    fi
else
    fail "scripts/smoke_outreach.sh not found" "expected at scripts/smoke_outreach.sh"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

printf "\n"
if [ "$FAILED" -eq 0 ]; then
    printf "${GREEN}${BOLD}All checks passed.${RESET} Ready for first run.\n"
    printf "${DIM}Next: python outreach/pipeline.py --stage all --dry-run${RESET}\n"
    exit 0
else
    printf "${RED}${BOLD}Some checks failed.${RESET} Fix the issues above and re-run:\n"
    printf "  bash scripts/setup.sh\n"
    exit 1
fi
