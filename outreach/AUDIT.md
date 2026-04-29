# outreach/AUDIT.md — End-to-End Audit (2026-04-29)

Auditor: Claude Code (claude-sonnet-4-6)
Scope: outreach/ module, .claude/agents/, related .gitignore and pipeline behaviour
Methodology: read all source files, run pytest, verify imports, test `claude --agent` flag,
run `python outreach/pipeline.py --stage all --dry-run`, check directory structure, check .gitignore.

---

## 1. TEST RESULTS

```
pytest -v
132 passed, 0 failed, 0 skipped
7.61 seconds
```

Relevant test modules and what they cover:

| File | What's tested |
|---|---|
| `tests/test_tracker.py` | upsert idempotency, state machine illegal transitions, dedupe check, count_sent_today |
| `tests/test_timing.py` | country_to_tz, next_send_window_utc (weekends, windows, UTC fallback) |
| `tests/test_scorer.py` | tier classification, scoring rubric, hard skips, threshold enforcement |
| `tests/test_logger.py` | CSV logger, URL normalisation, dedupe, count_today |
| `tests/test_browser.py` | BotDetectionError, human_type, create_browser_context |
| `tests/test_integration.py` | apply-bot end-to-end flows (not outreach-specific) |

All outreach-specific units (tracker, timing) pass cleanly.

---

## 2. MODULE IMPORTS

All resolve without errors:

```
python3 -c "from outreach.lib import tracker, sender, gmail_pool, timing"  → OK
python3 -c "from outreach import pipeline"                                  → OK
```

Note: `outreach/` has no `__init__.py`. Imports succeed via Python 3.3+ namespace packages.
This works on CPython but may behave unexpectedly with some static analysis tools or
if the package is ever installed (e.g., `pip install -e .`). Not a runtime issue right now.

---

## 3. SUBAGENT FILES

All four agent files exist at `.claude/agents/`:

| Agent | File | Frontmatter complete | model=sonnet | References GUARDRAILS.md | References outreach/CLAUDE.md |
|---|---|---|---|---|---|
| role_researcher | `.claude/agents/role_researcher.md` | ✓ | ✓ | ✓ (§1.7 named) | ✓ (§2, §4, §5) |
| people_finder | `.claude/agents/people_finder.md` | ✓ | ✓ | ✓ (§1.7 named) | ✓ (§2, §4, §5) |
| channel_finder | `.claude/agents/channel_finder.md` | ✓ | ✓ | ✓ (§1.7 named) | ✓ (§2, §4, §5) |
| message_writer | `.claude/agents/message_writer.md` | ✓ | ✓ | ✓ (§1.7 named) | ✓ (pipeline context) |

Frontmatter fields present on all four: `name`, `description`, `model`, `tools`.
Tools declared: `Read, Write, Bash, WebFetch, WebSearch` on all four.

---

## 4. CLAUDE CODE --agent FLAG

```
claude --agent role_researcher --print -p "Print exactly the string ACK and stop. ..."
→ ACK
```

The `--agent` flag is supported by the installed Claude Code version.
The pipeline orchestrator (`outreach/pipeline.py`) depends on this flag for all four stages.
**No critical finding here.**

---

## 5. PIPELINE DRY-RUN

```
python3 outreach/pipeline.py --stage all --dry-run
```

```
[INFO] Starting stage: research (agent: role_researcher) [DRY RUN]
[INFO] Stage research complete: 13 processed, 0 errored (0%), exit=0
[INFO] Starting stage: people (agent: people_finder) [DRY RUN]
[INFO] Stage people complete: 5 processed, 2 errored (40%), exit=0
[ERROR] Stage 'people' error rate 40% exceeds 30% threshold — halting pipeline
[INFO] [DRY RUN] Pipeline complete. Stages run: 2/4
```

The pipeline halted at the `people` stage before running `channel` or `write`.

### Why it halted — root cause

`_parse_error_rate()` in `pipeline.py` (line 107) counts any table row that mentions
`"error"`, `"failed"`, or `"skipped"` as an errored row:

```python
errored = sum(
    1
    for row in data_rows
    if re.search(r"\b(error|failed|skipped)\b", row, re.IGNORECASE)
)
```

`"skipped"` is a **normal outcome** for agents. The people_finder legitimately outputs
"skipped" rows when companies have no public team presence (stealth startups, etc.).
The dry-run showed 5 table rows, 2 of which mentioned "skipped" → 40% > 30% threshold → halt.

This is a **logic bug**: the error rate gate was designed to catch agent failures, not
to penalise normal filtering behaviour. In a live pipeline run with real data, any batch
where >30% of companies are stealth or low-profile will falsely halt the pipeline.

**See §8 (critical findings) below.**

---

## 6. DIRECTORY STRUCTURE

| Path | Expected | Actual | Notes |
|---|---|---|---|
| `outreach/data/` | exists | ✓ exists | — |
| `outreach/data/tracker.csv` | exists, header row present | ✓ header row only (empty data) | Correct starting state |
| `outreach/data/drafts/` | should exist | ✗ missing | message_writer creates it if absent (per agent instructions). Not created at init time. |
| `outreach/data/sent/` | should exist | ✗ missing | sender.py `_archive_eml()` creates date-subdirs dynamically. Not created at init time. |
| `secrets/` | should exist (OAuth tokens live here) | ✗ missing | setup_gmail_oauth.py writes here; InboxPool reads from here. Needs to exist before first use. |

`drafts/` and `sent/` missing is not hard-broken — both are created on demand by the code.
`secrets/` missing means the sender will raise `FileNotFoundError` on first real tick
if `.env.outreach` points into `secrets/` before the directory is created via OAuth setup.

---

## 7. GITIGNORE COVERAGE

| Item | In .gitignore | Notes |
|---|---|---|
| `secrets/` | ✓ | Covered |
| `.env.outreach` | ✓ | Covered |
| `outreach/data/` | ✓ | Covered — tracker, drafts, sent, all under this |
| `outreach/STOP` | ✗ **missing** | If this file is accidentally committed, the sender is permanently disabled until someone deletes it from git history. Low-probability but high-consequence. |
| `*.token.json` | ✗ missing | OAuth tokens stored in `secrets/` are covered by `secrets/`. But if a token is saved outside `secrets/` (e.g., to the project root during OAuth setup iteration), it would be committed. The setup_gmail_oauth.py defaults to `secrets/{address}.token.json` so current code is safe, but the pattern isn't gitignored as a safety net. |

---

## 8. CRITICAL FINDINGS (block further work)

### CRITICAL-1: `_parse_error_rate` treats "skipped" as an error

**File:** `outreach/pipeline.py`, lines 92–112 (`_parse_error_rate`)

**Problem:**
The regex `r"\b(error|failed|skipped)\b"` counts any table row containing "skipped"
as an errored row. Agents output "skipped" for rows that were intentionally not
processed (stealth companies, no public profiles, cooldown, etc.). These are
**correct behaviour**, not failures.

**Effect in dry-run:** people stage appeared at 40% error rate → pipeline halted.
**Effect in production:** any pipeline run where >30% of companies have no findable
public team members will halt after the people stage, blocking channel_finder and
message_writer from running. This is the typical case for a cold pool — stealth startups,
pre-launch companies, and companies with minimal LinkedIn presence are common.

**Reproduction:**
```
python3 outreach/pipeline.py --stage people --dry-run
# Will halt if >30% of research_done rows produce "skipped" people_search_failed outputs
```

**What a fix would look like:** exclude `"skipped"` from the regex, or count only rows
where the final status column (e.g., the last `|`-delimited cell) says `error` or `failed`.
This is a one-function change but needs a test.

---

## 9. NON-CRITICAL FINDINGS (ambiguous or low-severity)

### A. All pipeline paths are relative, no CWD enforcement

`pipeline.py` uses `Path("outreach/STOP")`, `Path("data/logs")`, etc. — all relative.
If the script is invoked from any directory other than the repo root
(`cd outreach && python pipeline.py --stage all`), paths silently fail.
The apply-bot's `apply.py` has the same pattern. Convention-over-enforcement, but
worth a comment or a guard `assert Path('CLAUDE.md').exists(), "Run from repo root"`.

### B. `upsert()` deduplicates on (company, person_name); blank person_name collapses rows

`tracker.py:upsert()` matches on `(company.lower(), person_name.lower())`.
For `research_done` rows (inserted by Agent 1), `person_name` is empty.
If Agent 1 tries to insert a second row for the same company (e.g., a different role at
the same company), the upsert silently merges it into the existing row rather than
creating a new one. The role_researcher dedupe logic (check by `role_url` first, then
company recency) prevents this in practice, but the tracker itself doesn't enforce
uniqueness on `role_url`, so a direct-code path that skips the dedupe check would
silently collapse rows.

### C. `mark_sent` does two separate read-write cycles (TOCTOU)

`tracker.py:mark_sent()` calls `update_status()` (read → write), then does a second
`_read_all_raw()` to set `sent_at_utc` and `assigned_inbox` (read → write again).
Between the two writes, another process could modify the file. Low risk given
the sender runs single-threaded, but the two-cycle pattern is unnecessary and could
be collapsed to one write.

### D. Pipeline logs go to `data/logs/`, not `outreach/data/logs/`

`pipeline.py:_LOG_DIR = Path("data/logs")` — this is in the main apply-bot log directory,
not under `outreach/`. It is covered by the `data/` gitignore entry.
Not wrong, but undocumented. If someone expects outreach pipeline logs under
`outreach/data/`, they won't find them.

### E. `outreach/STOP` undocumented for manual kill

`guidelines.md §3.8` says "Stop-file: `outreach/STOP` — if this file exists, the sender
exits immediately." But there is no documented command to create it (unlike `data/STOP`
which has `touch data/STOP` documented in guidelines.md §6). Minor UX gap.

### F. `outreach/data/drafts/` and `outreach/data/sent/` not created at init

`tracker.py:init_tracker()` creates `outreach/data/tracker.csv` if absent.
There is no equivalent `init_outreach()` that creates `drafts/` and `sent/`.
Both are created on demand by the code that uses them, so this is not a bug.
Worth noting for anyone running `tree outreach/` and wondering why the documented
directory structure doesn't match `outreach/CLAUDE.md §3`.

---

## 10. WHAT WORKS

- **All 132 tests pass.** Core logic (state machine, deduplication, timing, scoring) is well-covered.
- **All imports resolve.** No missing modules, no circular imports, no broken `__init__.py`.
- **`claude --agent` works.** Pipeline's subprocess invocation pattern is viable.
- **State machine is enforced.** `drafted → sent` and other illegal transitions raise `StateMachineError` (tested).
- **Guardrails are encoded in code.** Generic alias filter (`_BANNED_PREFIXES`) matches the list in GUARDRAILS.md §1.7. Daily cap check runs before and during each tick. STOP-file is checked at every stage boundary.
- **Send windows are correct.** `timing.py` respects 10:00–11:00 / 14:00–15:00 local, skips weekends, picks a random minute within the window.
- **File locking present.** `tracker.py:_write_all()` uses `fcntl.flock(LOCK_EX)` on write.
- **Agent files are well-authored.** Frontmatter correct, instructions detailed, cross-references to GUARDRAILS.md accurate, hard rules explicit in each agent's §"What You Must Not Do".
- **`principles.md` and examples are solid.** Good/bad examples directly illustrate the spec. Forbidden phrase list matches `message_writer.md §4`.
- **`.gitignore` covers the high-risk items** (credentials, tracker data, OAuth tokens via `secrets/`).

---

## 11. SUMMARY TABLE

| # | Item | Severity | Status |
|---|---|---|---|
| CRITICAL-1 | `_parse_error_rate` counts "skipped" as errors → false pipeline halts | **Critical** | Unfixed — needs one-function change + test |
| A | Relative paths, no CWD enforcement | Low | Unfixed — convention risk |
| B | `upsert()` collapses blank-person_name rows for same company | Low | Unfixed — mitigated by agent dedupe logic |
| C | `mark_sent` two-write-cycle TOCTOU | Low | Unfixed — low risk in practice |
| D | Pipeline logs land in `data/logs/`, not `outreach/data/logs/` | Low | Undocumented |
| E | No documented `touch outreach/STOP` kill command | Low | Documentation gap |
| F | `drafts/` and `sent/` dirs absent at start | Low | Created on demand; no runtime impact |
| G | `outreach/STOP` not gitignored | Low | .gitignore gap |
| H | `*.token.json` not gitignored | Low | Mitigated by `secrets/` coverage |
| I | No `outreach/__init__.py` | Informational | Works via namespace packages |
