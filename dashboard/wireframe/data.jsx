// Shared mock data for the wireframes.
// Stress-test volumes — 30+ items per pipeline column, lots of drafts, busy log tail.

const COMPANIES = [
  "Anthropic", "OpenAI", "Stripe", "Linear", "Vercel", "Figma", "Notion", "Ramp",
  "Mercury", "Plaid", "Brex", "Retool", "Airtable", "Loom", "Cursor", "Replit",
  "Hex", "Modal", "Pinecone", "Weaviate", "LangChain", "Hugging Face", "Cohere",
  "Mistral", "Perplexity", "Glean", "Harvey", "Sierra", "Decagon", "Adept",
  "Runway", "Pika", "Suno", "ElevenLabs", "Character", "Inflection", "Scale",
  "Databricks", "Snowflake", "Confluent", "MongoDB", "Datadog", "PagerDuty",
  "Sentry", "Honeycomb", "Grafana", "Cloudflare", "Fly.io", "Render", "Railway",
];

const ROLES = [
  "Product Designer", "Sr. Product Designer", "Staff Designer", "Design Engineer",
  "UX Engineer", "Sr. Design Engineer", "Founding Designer", "Design Lead",
  "Principal Designer", "Senior UX Engineer", "Frontend Engineer", "Sr. Frontend",
];

const FIRST = ["Alex", "Sam", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Jamie",
  "Avery", "Quinn", "Blake", "Devon", "Reese", "Skyler", "Kai", "Rowan",
  "Sage", "Phoenix", "Hayden", "Parker", "Emerson", "Drew", "Finley", "Sutton"];
const LAST = ["Chen", "Patel", "Kim", "Singh", "Garcia", "Nguyen", "Park", "Lee",
  "Brown", "Davis", "Wilson", "Martin", "Rivera", "Cohen", "Wright", "Khan",
  "Murphy", "Bauer", "Reyes", "Walsh", "Chang", "Okafor", "Yamada", "Becker"];

function rand(seed) {
  // tiny mulberry32 — deterministic so the wireframe is stable across reloads
  let t = seed + 0x6D2B79F5;
  return () => {
    t |= 0; t = (t + 0x6D2B79F5) | 0;
    let r = Math.imul(t ^ (t >>> 15), 1 | t);
    r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
  };
}

function pick(arr, r) { return arr[Math.floor(r() * arr.length)]; }

const COLUMNS = [
  { id: "research_done",  label: "research_done",  wip: 60 },
  { id: "people_found",   label: "people_found",   wip: 50 },
  { id: "contact_found",  label: "contact_found",  wip: 40 },
  { id: "drafted",        label: "drafted",        wip: 30 },
  { id: "queued",         label: "queued",         wip: 25 },
  { id: "sent",           label: "sent",           wip: 80 },
  { id: "replied",        label: "replied",        wip: 20 },
];

function buildPipeline() {
  const r = rand(7);
  const counts = [34, 31, 30, 33, 28, 41, 18];
  const out = {};
  let id = 1000;
  COLUMNS.forEach((col, ci) => {
    const items = [];
    for (let i = 0; i < counts[ci]; i++) {
      const tier = (Math.floor(r() * 100) < 35) ? 1 : (Math.floor(r() * 100) < 60 ? 2 : 3);
      const days = Math.floor(r() * 14);
      items.push({
        id: `OUT-${id++}`,
        company: pick(COMPANIES, r),
        role: pick(ROLES, r),
        person: `${pick(FIRST, r)} ${pick(LAST, r)}`,
        tier,
        column: col.id,
        days,
        lastAction: days === 0 ? "today" : days === 1 ? "1d ago" : `${days}d ago`,
        stale: days >= 5,
      });
    }
    out[col.id] = items;
  });
  return out;
}

const PIPELINE = buildPipeline();

// ── Drafts ────────────────────────────────────────────────────────────────
const HOOKS = [
  "Saw your talk on design-engineering at Config — the bit on tokens-as-API stuck with me.",
  "Your team's component-library rewrite post is one of the best engineering writeups I've read this year.",
  "Noticed you're hiring a Design Engineer — the role reads like it was written for the work I've been doing.",
  "Loved the recent dashboard redesign — particularly how you handled the empty states.",
  "Your essay on 'designers who code' shaped how I think about the role.",
  "Caught your podcast appearance last week — the framing on AI-native UX was sharp.",
  "Your team shipped a 4× perf improvement on the editor — I'd love to learn how.",
  "Read your hiring post — the emphasis on prototype-driven design matched my workflow exactly.",
  "Saw the new mobile flow — the gesture-first approach is bold and it works.",
  "Your write-up on internal tooling for ML teams was the clearest I've seen.",
];

const BODIES = [
  `Hey {first},\n\n{hook}\n\nI'm a design engineer with 6 years building 0→1 product at small teams (last role at a Series B). Recently shipped: a fully-keyboard-driven editor (a la Linear), an AI-assisted onboarding flow that took activation from 31% → 58%, and a real-time collaborative whiteboard.\n\nI noticed {company} is hiring for {role}. Would love 20 minutes to hear what the team is shaping next — happy to share my portfolio + a teardown of one of your surfaces if useful.\n\nNo pressure either way. Best,\nMe`,
  `Hi {first} —\n\n{hook}\n\nQuick intro: design engineer, ~6 yrs, mostly Series A/B startups. I tend to live in the seam between Figma and the codebase. Most recent thing I'm proud of: rewrote my last company's design system and shipped it across 4 product surfaces in 6 weeks.\n\nWith {company} hiring for {role}, I figured it was worth a note. Would a 20-min intro chat work in the next two weeks? I can come prepared with specific questions about the role and an honest take on a surface or two.\n\nThanks for reading,\nMe`,
  `{first},\n\n{hook}\n\nI'll keep this short: I'm looking for my next role, and {company}'s {role} posting is the one I keep coming back to. Background: 6 years design-engineering at small startups, comfortable owning surface area end-to-end (research → ship), strong React/TS, and I prototype in code.\n\nWould you be open to a brief intro call? Happy to send portfolio + a writeup of how I'd approach a real {company} surface ahead of time.\n\nBest,\nMe`,
];

const REASONS = [
  "Hiring for a role that matches my level + scope",
  "Authored the recent design-system rewrite — direct overlap with my work",
  "Manages the team I'd want to join based on the JD",
  "Gave a recent talk on the exact problem space I prototype in",
  "Mentioned hiring on a podcast last month",
  "Frequent author on the eng blog — high signal that they value writing",
];

const PRIOR_OUTREACH = [
  [],
  [{ when: "Apr 2024", channel: "LinkedIn", outcome: "no reply" }],
  [{ when: "Nov 2024", channel: "Email", outcome: "polite decline — not hiring" }],
  [
    { when: "Jan 2025", channel: "Email", outcome: "intro chat (45m)" },
    { when: "Feb 2025", channel: "Email", outcome: "passed — closed role" },
  ],
];

function buildDrafts(n = 14) {
  const r = rand(42);
  const out = [];
  for (let i = 0; i < n; i++) {
    const company = pick(COMPANIES, r);
    const role = pick(ROLES, r);
    const first = pick(FIRST, r);
    const person = `${first} ${pick(LAST, r)}`;
    const hook = pick(HOOKS, r);
    const body = pick(BODIES, r)
      .replaceAll("{first}", first)
      .replaceAll("{hook}", hook)
      .replaceAll("{company}", company)
      .replaceAll("{role}", role);
    const tier = (Math.floor(r() * 100) < 35) ? 1 : (Math.floor(r() * 100) < 60 ? 2 : 3);
    const score = Math.floor(40 + r() * 60); // 40–100
    out.push({
      id: `DRAFT-${2000 + i}`,
      company, role, person, first, hook, body, tier,
      score,
      reason: pick(REASONS, r),
      prior: PRIOR_OUTREACH[Math.floor(r() * PRIOR_OUTREACH.length)],
      generated: `${Math.floor(r() * 6) + 1}h ago`,
    });
  }
  return out;
}

const DRAFTS = buildDrafts(14);

// ── Agent activity ────────────────────────────────────────────────────────
const AGENTS = ["researcher", "person-finder", "contact-finder", "drafter", "sender"];
const ACTIONS = {
  "researcher": ["fetch_jobs_page", "parse_jd", "score_company", "extract_signals", "skip_company"],
  "person-finder": ["search_linkedin", "rank_candidates", "select_target", "skip_company"],
  "contact-finder": ["lookup_email", "verify_smtp", "fallback_pattern", "no_contact_found"],
  "drafter": ["generate_hook", "generate_body", "score_draft", "regen_draft", "manager_review"],
  "sender": ["queue_send", "send_email", "log_bounce", "track_open", "track_reply"],
};
const STATUSES = ["ok", "ok", "ok", "ok", "ok", "warn", "ok", "ok", "ok", "error"]; // weighted

const MESSAGES = {
  ok: [
    "completed in {ms}ms",
    "200 OK · {ms}ms",
    "found {n} candidates",
    "score={score} (threshold 60)",
    "matched template tone",
    "verified MX record",
    "queued for 09:30 PT",
    "draft written, 142 tokens",
    "indexed 3 new signals",
  ],
  warn: [
    "rate-limit headers seen, backing off {ms}ms",
    "soft-bounce — retrying",
    "low confidence (0.42) — flagging for manager review",
    "no LinkedIn match, falling back to Apollo",
    "ambiguous role title — picked closest",
  ],
  error: [
    "503 from upstream",
    "timeout after 30000ms",
    "no contact found — moved to needs_human",
    "send failed: hard-bounce",
    "schema mismatch in response payload",
  ],
};

function buildLogs(n = 220) {
  const r = rand(13);
  const out = [];
  let t = Date.now() - n * 7000;
  for (let i = 0; i < n; i++) {
    const agent = pick(AGENTS, r);
    const action = pick(ACTIONS[agent], r);
    const status = pick(STATUSES, r);
    const company = pick(COMPANIES, r);
    const tmpl = pick(MESSAGES[status], r);
    const message = tmpl
      .replaceAll("{ms}", String(50 + Math.floor(r() * 1900)))
      .replaceAll("{n}", String(1 + Math.floor(r() * 8)))
      .replaceAll("{score}", String(40 + Math.floor(r() * 60)));
    t += 1000 + Math.floor(r() * 12000);
    const d = new Date(t);
    const ts = d.toISOString().slice(11, 19);
    out.push({
      id: i,
      ts,
      ms: String(d.getMilliseconds()).padStart(3, "0"),
      agent,
      action,
      target: company,
      status,
      message,
    });
  }
  return out.reverse(); // newest first
}

const LOGS = buildLogs(220);

Object.assign(window, { COLUMNS, PIPELINE, DRAFTS, LOGS, AGENTS, COMPANIES, ROLES });
