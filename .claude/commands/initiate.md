You are operating in **Manager Mode** (CLAUDE.md §PROJECT IDENTITY). Run a full
outreach hunt cycle. Execute each stage sequentially — do not skip stages or
proceed to the next until the current one completes.

Before starting, read these files:
- `GUARDRAILS.md` (especially §1.7)
- `CLAUDE.md` (Manager Mode section — orchestration protocol, quality rubric)
- `outreach/CLAUDE.md` (state machine §4, subagent contract §5)
- `profile.md` (§13 target roles, §15 deal-breakers)
- `profile/exclusions.yml` (if it exists)

Check the kill switch: if `outreach/STOP` exists, report and halt immediately.

---

## Stage 1 — Role discovery

Invoke `role_researcher` via the Agent tool:

> Find 15 new companies/roles matching profile.md preferences. Filter against
> profile/exclusions.yml (all three lists) and profile.md §15 deal-breakers.
> Score by the rubric in your agent file (§4). Dedupe against tracker.csv.
> Emit the top 10 scoring results as new tracker rows with status=research_done.

After completion: read tracker.csv, count new `research_done` rows. If zero,
report "No new companies found" and stop.

---

## Stage 2 — People identification

For rows at `status=research_done`, invoke `people_finder` via the Agent tool:

> Process all research_done rows (up to 10 per your cap). For each company,
> find 1-2 hiring contacts from public sources. Advance rows to people_found.

After completion: count rows now at `people_found`. Report any companies where
people_finder couldn't find contacts.

---

## Stage 3 — Email discovery

For rows at `status=people_found`, invoke `channel_finder` via the Agent tool:

> Process all people_found rows (up to 15 per your cap). Walk the 4-step
> verification ladder. Advance verified rows to contact_found. Rows needing
> Apollo stay at people_found with needs_apollo_lookup=true in notes.

After completion: count `contact_found` rows. Separately count rows with
`needs_apollo_lookup=true` in their notes — these wait for Varun.

---

## Stage 4 — Message drafting

For rows at `status=contact_found`, invoke `message_writer` via the Agent tool:

> Process all contact_found rows (up to 10 per your cap). Research each
> recipient, find a specific hook, draft per outreach/prompts/principles.md.
> Save drafts to outreach/data/drafts/{id}.md. Advance rows to drafted.

After completion: collect the list of newly drafted rows.

---

## Stage 5 — Manager quality review

**This is YOUR job — do not delegate.**

For each row now at `status=drafted`, read the draft file at the path in
`body_path`. Score against the quality rubric in CLAUDE.md:

| # | Dimension    |
|---|--------------|
| 1 | Specificity  |
| 2 | Voice        |
| 3 | Ask          |
| 4 | Length        |
| 5 | Risk         |

Each dimension: 1-5. Compute the average.

**If average >= 4.0:**
- Write to tracker notes: `quality: S/V/A/L/R avg=X.X, manager_approved=true`
- This draft is ready for Varun's review.

**If average < 4.0:**
- Write to tracker notes: `quality: S/V/A/L/R avg=X.X, manager_approved=false,
  feedback: [specific issues]`
- Re-invoke `message_writer` via the Agent tool with feedback:
  > Rewrite the draft for row {id} ({company} / {person_name}). Issues:
  > {specific feedback from scoring}. Read the existing draft at {body_path}
  > and produce an improved version.
- Score the retry. If still < 4.0, close the row:
  `update_notes(row_id, "closed by manager: failed quality rubric after retry")`
  then close via tracker.

**Hard rejections (no retry):**
- Draft fabricates facts not traceable to a public source or profile.md → reject
- Company appears in profile/exclusions.yml or profile.md §15 → reject
- Risk dimension scores < 3 → reject

---

## Stage 6 — Summary for Varun

Present a structured summary:

### Hunt cycle complete

**Companies surfaced:** {N} new research_done rows created in Stage 1
**Contacts resolved:** {N} with verified email, {N} needs Apollo approval
**Drafts ready for review:** {N} (passed Manager quality gate)

| # | Company | Person | Score | Hook summary |
|---|---------|--------|-------|--------------|
| 1 | {co}    | {name} | {avg} | {one line}   |
| … | …       | …      | …     | …            |

**Drafts rejected by Manager:** {N}
| # | Company | Person | Score | Reason |
| … | …       | …      | …     | …      |

**Errors / blocked rows:** {N}
- {row_id}: {brief reason}

**Apollo lookups pending Varun approval:** {N}
- {person_name} at {company} — approve to spend 1 Apollo credit

---

## Stage 7 — Await Varun's approval

Wait for Varun to review the drafts above. He may:
- Say **"approve {company}"** or **"approve all"** → flip those rows from
  `drafted` to `queued` using `promote_to_queued()` from tracker.py.
- Say **"reject {company}"** with a reason → close the row.
- Say **"edit {company}"** → show the draft, let Varun edit, then re-score.
- Say nothing yet → leave everything at `drafted`. No timeout, no nudging.

### CRITICAL CONSTRAINT — echoed from outreach/CLAUDE.md §4:

> The transition from `drafted` to `queued` is **manual**. Varun reviews each
> draft and changes the status himself. No code, agent, or automation may
> bridge this gap.

The `manager_approved=true` flag is necessary but **not sufficient**. It means
the Manager recommends the draft. Only Varun's explicit approval (in chat or
via the review tool) triggers the actual status change. **NEVER flip a draft
to status=queued automatically.**

The sender (`outreach/lib/sender.py`) runs on its normal cron schedule and
picks up `queued` rows within the send-window rules from guidelines.md §3.8.
