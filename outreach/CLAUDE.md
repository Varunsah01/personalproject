# outreach/CLAUDE.md — Outreach Module Instructions

> Module-level instructions for the cold-outreach pipeline. This file governs all subagents and code within `outreach/`. It **adds** module-specific rules but **never relaxes** anything in the parent guardrails or guidelines.

---

## 0. PRIORITY ORDER

1. `GUARDRAILS.md` (root) — especially **section 1.7** (outreach hard rules). Non-negotiable.
2. `guidelines.md` (root) — especially **section 2.4** (outreach tier mapping) and **section 3.8** (outreach rate limits).
3. `CLAUDE.md` (root) — project-wide architecture and conventions.
4. **This file** (`outreach/CLAUDE.md`) — module-specific overrides and schemas.
5. `profile.md` — Varun's personal data, voice, and facts.
6. The user's current message.

If two sources disagree, the higher-numbered source loses. Surface the conflict before acting.

---

## 1. PIPELINE OVERVIEW

Six stages, executed in order. Each stage advances rows in `tracker.csv` through the status state machine.

| Stage | Agent | Input status | Output status | What it does |
|---|---|---|---|---|
| 1. Role Researcher | `.claude/agents/role_researcher.md` | *(new row)* | `research_done` | Finds relevant openings from job boards and public sources; writes company, role_url, role_title, role_tier |
| 2. People Finder | `.claude/agents/people_finder.md` | `research_done` | `people_found` | Identifies hiring-team contacts (hiring manager, founder, team lead) from public sources; writes person_name, person_title, person_linkedin, person_country, relationship_type |
| 3. Channel Finder | `.claude/agents/channel_finder.md` | `people_found` | `contact_found` or `linkedin_queue` | Discovers email addresses from public sources, Apollo.io, Hunter.io; writes email, email_confidence, linkedin_only. Routes to `contact_found` (email found) or `linkedin_queue` (no email) |
| 3b. LinkedIn Writer | `.claude/agents/linkedin_writer.md` | `linkedin_queue` | `linkedin_queue` (body_path populated) | Drafts 2-sentence LinkedIn DM for contacts with no email; writes to `outreach/data/drafts/{id}_linkedin.md` |
| 4. Message Writer | `.claude/agents/message_writer.md` | `contact_found` | `drafted` | Crafts personalised message following `outreach/prompts/principles.md`; writes hook, subject, body_path (draft file at `outreach/data/drafts/{id}.md`) |
| 5. Sender | `outreach/lib/sender.py` | `queued` | `sent` | Sends via Gmail API through rotated inboxes within timing windows; writes assigned_inbox, send_at_utc, sent_at_utc |
| 6. Follow-Up Writer | `.claude/agents/follow_up_writer.md` | `sent` (7-14d, no reply) | `follow_up_drafted` | Drafts 2-sentence follow-up bumps; writes to `outreach/data/drafts/{id}_followup.md` |

**Critical gaps:** the transitions `drafted` → `queued` and `follow_up_drafted` → `follow_up_queued` are both **manual**. Varun reviews each draft and changes the status himself. No code, agent, or automation may bridge these gaps. The `linkedin_queue` → `closed` transition is also manual — Varun sends the DM via LinkedIn and marks the row as messaged through the dashboard.

---

## 2. TRACKER.CSV SCHEMA

Single source of truth: `outreach/data/tracker.csv`

| Column | Filled by | Notes |
|---|---|---|
| `id` | system | UUID |
| `company` | Agent 1 | from job opening |
| `role_url` | Agent 1 | JD link |
| `role_title` | Agent 1 | normalised |
| `role_tier` | Agent 1 | T1/T2/T3 from root `CLAUDE.md` section 3 |
| `person_name` | Agent 2 | hiring team / founder / team lead |
| `person_title` | Agent 2 | |
| `person_linkedin` | Agent 2 | |
| `person_country` | Agent 2 | for geo timing |
| `relationship_type` | Agent 2 | `hiring_manager` / `founder` / `team` / `referral` |
| `email` | Agent 3 | if found |
| `email_confidence` | Agent 3 | `high` / `medium` / `low` |
| `linkedin_only` | Agent 3 | `true` if no email found |
| `hook` | Agent 4 | personalised opener |
| `subject` | Agent 4 | |
| `body_path` | Agent 4 | path to drafted `.md` file |
| `status` | system | see state machine below |
| `assigned_inbox` | Agent 5 | which Gmail ID sent it |
| `send_at_utc` | Agent 5 | scheduled send time |
| `sent_at_utc` | Agent 5 | actual send timestamp |
| `replied` | manual | Varun marks |
| `notes` | any | freeform |
| `last_updated` | system | ISO timestamp, for audit + dedupe |
| `message_id` | Agent 5 | RFC 5322 Message-ID of the sent email (for In-Reply-To threading) |

---

## 3. FILE MAP

```
outreach/
├── CLAUDE.md                          # this file
├── STOP                               # if present, sender exits immediately
├── prompts/
│   └── principles.md                  # voice + craft rules for all drafts
├── lib/
│   ├── tracker.py                     # all tracker.csv reads/writes go through here
│   └── sender.py                      # Gmail OAuth, inbox rotation, geo timing, send logic
├── data/
│   ├── tracker.csv                    # single source of truth for all outreach rows
│   ├── drafts/
│   │   └── {id}.md                    # one draft file per message, referenced by body_path
│   └── sent/
│       └── {date}/
│           └── {id}.eml              # archived copy of each sent message
└── pipeline.py                        # orchestrator: runs stages 1–4 in sequence
```

---

## 4. STATUS STATE MACHINE

### States (primary flow — email)

`research_done` → `people_found` → `contact_found` → `drafted` → `queued` → `sent` → `replied` → `closed`

### States (LinkedIn branch)

`people_found` → `linkedin_queue` → `closed`

### States (follow-up branch)

`sent` → `follow_up_drafted` → `follow_up_queued` → `follow_up_sent` → `replied` → `closed`

### Legal transitions

| From | To | Triggered by | Notes |
|---|---|---|---|
| *(new)* | `research_done` | Agent 1 (role_researcher) | Row created with company, role_url, role_title, role_tier |
| `research_done` | `people_found` | Agent 2 (people_finder) | person_* fields populated |
| `people_found` | `contact_found` | Agent 3 (channel_finder) | When email found (high/medium confidence) |
| `people_found` | `linkedin_queue` | Agent 3 (channel_finder) | When `linkedin_only=true` — no email found |
| `linkedin_queue` | `closed` | **Varun (manual)** | After sending LinkedIn DM via dashboard |
| `contact_found` | `drafted` | Agent 4 (message_writer) | hook, subject, body_path populated; draft file written |
| `drafted` | `queued` | **Varun (manual only)** | Human review gate. No automation may perform this transition. |
| `queued` | `sent` | Agent 5 (sender) | Only within send windows; only if under daily cap |
| `sent` | `follow_up_drafted` | Agent 6 (follow_up_writer) | After 7-14 days with no reply; follow-up draft written |
| `sent` | `replied` | reply_watcher / **Varun** | Reply detected or manually marked |
| `follow_up_drafted` | `follow_up_queued` | **Varun (manual only)** | Human review gate — same as `drafted` → `queued`. |
| `follow_up_queued` | `follow_up_sent` | Agent 5 (sender) | Same send logic; includes In-Reply-To header for threading |
| `follow_up_sent` | `replied` | reply_watcher / **Varun** | Reply detected after follow-up |
| `replied` | `closed` | **Varun (manual)** | Conversation concluded |
| *any* | `closed` | **Varun (manual)** | Can close at any point (e.g., role filled, not interested) |

**Illegal transitions:** any transition not listed above. In particular:
- `drafted` → `sent` is **never** legal. Must pass through `queued` via manual approval.
- `follow_up_drafted` → `follow_up_sent` is **never** legal. Must pass through `follow_up_queued` via manual approval.
- No backward transitions (e.g., `sent` → `drafted`). If a re-send is needed, create a new row.
- No second follow-up: `follow_up_sent` cannot transition to `follow_up_drafted`.

---

## 5. SUBAGENT CONTRACT

Every subagent (Agents 1–4, 6) operating within this module must:

1. **Read first.** Before doing any work, read `outreach/prompts/principles.md` and `GUARDRAILS.md` section 1.7. Non-negotiable.
2. **Never invent data.** If a field cannot be verified from a public source, leave it blank. Never guess email addresses, titles, company details, or relationship types.
3. **Write via tracker.py only.** All reads and writes to `tracker.csv` go through `outreach/lib/tracker.py`. No direct CSV manipulation.
4. **Never send mail.** No subagent has permission to send any communication. Only `outreach/lib/sender.py` sends, and only for rows with status `queued`.
5. **One stage per run.** Each agent advances rows through exactly one status transition. Don't skip stages.
6. **Log everything.** Update the `notes` field with context on decisions made (e.g., "skipped — no public email found", "hook based on founder's April 2026 LinkedIn post about Series A").
7. **Respect the 14-day cooldown.** Before creating a new row for a person, check `tracker.csv` for any row with the same `email` or `person_linkedin` that has `sent_at_utc` within the last 14 days. If found, skip.

---

## 6. REFERENCES

For anything not specified in this file, defer to:

- **Target roles, tiers, and scoring:** root `CLAUDE.md` section 3
- **Hard rules (what's forbidden):** `GUARDRAILS.md`, especially section 1.7
- **Tier mapping (Green/Yellow/Red):** `guidelines.md` section 2.4
- **Rate limits and timing:** `guidelines.md` section 3.8
- **Varun's background and voice:** `profile.md`
- **Conventions (Python, logging, errors, tests):** root `CLAUDE.md` section 11
