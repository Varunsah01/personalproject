# Outreach Dashboard

Localhost-only Next.js dashboard for the outreach pipeline. Reads from a SQLite mirror of `tracker.csv`, polls every 5 seconds.

## Pages

- **`/`** — Pipeline kanban (7 status columns, search, tier filter, card drawer)
- **`/drafts`** — Approval queue (mobile-optimized, approve/reject with reason chips)
- **`/agents`** — Agent activity log (structured table, filter by agent/status)

## Setup

### 1. Install dependencies

```bash
cd dashboard/web
npm install
```

### 2. Sync tracker.csv to SQLite

The dashboard reads from `outreach/data/pipeline.db`. Run the sync script to populate it:

```bash
# One-shot sync
python3 outreach/lib/sync_to_sqlite.py

# Watch mode (loops every 60s) — run in a separate terminal
python3 outreach/lib/sync_to_sqlite.py --watch
```

### 3. Start the dev server

```bash
cd dashboard/web
npm run dev
```

Opens at `http://127.0.0.1:3000` (localhost only, not exposed to LAN).

### 4. Cron (production)

Sync tracker.csv to SQLite every 60 seconds:

```cron
* * * * * cd /Users/varunsah/Code/Job\ Automation && python3 outreach/lib/sync_to_sqlite.py >> /tmp/sync_to_sqlite.log 2>&1
```

## Data flow

```
tracker.csv  ──(sync_to_sqlite.py, every 60s)──>  pipeline.db
                                                      │
                                            Next.js reads (5s poll)
                                                      │
                                                  Dashboard UI
                                                      │
                                          Approve/Reject actions
                                                      │
                                     ┌────────────────┴────────────────┐
                                     ▼                                 ▼
                              pipeline.db                       tracker.csv
                           (SQLite update)                  (writeback.py via
                                                            tracker.py module)
```

**Source of truth:** `tracker.csv`. The SQLite database is a read-optimized mirror. Approve/reject actions write to both.

## Stack

- Next.js 15 + React 19 + TypeScript
- Tailwind v4 (dark theme matching wireframes)
- SQLite via better-sqlite3 (sync reads, WAL mode)
- Python sync script reuses existing `outreach/lib/tracker.py`
