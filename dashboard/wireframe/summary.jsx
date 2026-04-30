/* eslint-disable */
// Summary dashboard — overview of all work + per-agent task counts.
// Three variations: A overview, B agent-focused board, C compact ops report.

function aggregate() {
  const logs = window.LOGS;
  const pipeline = window.PIPELINE;
  const drafts = window.DRAFTS;

  // per-agent
  const byAgent = {};
  window.AGENTS.forEach(a => {
    byAgent[a] = { total: 0, ok: 0, warn: 0, error: 0, actions: {} };
  });
  logs.forEach(l => {
    const b = byAgent[l.agent];
    if (!b) return;
    b.total++;
    b[l.status]++;
    b.actions[l.action] = (b.actions[l.action] || 0) + 1;
  });

  // pipeline counts
  const colCounts = {};
  let totalActive = 0;
  let totalStale = 0;
  Object.entries(pipeline).forEach(([k, arr]) => {
    colCounts[k] = arr.length;
    totalActive += arr.length;
    totalStale += arr.filter(c => c.stale).length;
  });

  // tier breakdown across pipeline
  let t1 = 0, t2 = 0, t3 = 0;
  Object.values(pipeline).flat().forEach(c => {
    if (c.tier === 1) t1++;
    else if (c.tier === 2) t2++;
    else t3++;
  });

  // 24-hour spark — synthesize from logs (chunk into 24 buckets)
  const buckets = new Array(24).fill(0);
  logs.forEach((l, i) => {
    buckets[Math.floor((i / logs.length) * 24)]++;
  });

  return {
    byAgent,
    colCounts,
    totalActive,
    totalStale,
    totalLogs: logs.length,
    okRate: Math.round((logs.filter(l => l.status === "ok").length / logs.length) * 100),
    errRate: Math.round((logs.filter(l => l.status === "error").length / logs.length) * 100),
    warnRate: Math.round((logs.filter(l => l.status === "warn").length / logs.length) * 100),
    pendingDrafts: drafts.length,
    t1, t2, t3,
    buckets,
  };
}

// ── Summary nav ──────────────────────────────────────────────────
function SumNav() {
  return (
    <div className="topnav">
      <div className="brand mono">~/outreach</div>
      <div className="links">
        <a className="active">summary</a>
        <a>pipeline</a>
        <a>drafts</a>
        <a>activity</a>
      </div>
      <div className="right mono">localhost:3000 · last sync 14s ago</div>
    </div>
  );
}

// ── Variation A: overview-first ──────────────────────────────────
function SummaryA() {
  const s = aggregate();
  const Stat = ({ label, value, sub, accent }) => (
    <div style={{
      background: "var(--bg-1)", border: "1px solid var(--line)", borderRadius: 6,
      padding: 16, display: "flex", flexDirection: "column", gap: 4, minWidth: 0,
    }}>
      <div className="upper tiny dim2">{label}</div>
      <div className="mono" style={{ fontSize: 28, color: accent || "var(--ink)", lineHeight: 1.1 }}>{value}</div>
      {sub && <div className="mono tiny dim">{sub}</div>}
    </div>
  );

  const maxAgent = Math.max(...Object.values(s.byAgent).map(b => b.total));

  return (
    <div className="desktop-shell">
      <SumNav />
      <div style={{ flex: 1, overflowY: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 20 }}>
        {/* top stat strip */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 12 }}>
          <Stat label="active outreach" value={s.totalActive} sub={`${s.t1} T1 · ${s.t2} T2 · ${s.t3} T3`} />
          <Stat label="drafts pending" value={s.pendingDrafts} sub="awaiting approval" />
          <Stat label="agent tasks (24h)" value={s.totalLogs} sub={`${s.okRate}% ok`} />
          <Stat label="errors" value={s.errRate + "%"} sub={`${s.warnRate}% warn`} accent={s.errRate > 5 ? "var(--err)" : "var(--ink)"} />
          <Stat label="stale ≥ 5d" value={s.totalStale} sub="across all columns" accent="var(--warn)" />
        </div>

        {/* two-column row: agents table + pipeline funnel */}
        <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 12, minHeight: 0 }}>
          {/* agent table */}
          <div style={{ background: "var(--bg-1)", border: "1px solid var(--line)", borderRadius: 6, padding: 16 }}>
            <div style={{ display: "flex", alignItems: "baseline", marginBottom: 12, gap: 10 }}>
              <span className="mono" style={{ fontSize: 14 }}>tasks by agent</span>
              <span className="mono tiny dim2">last 24h · {s.totalLogs} total</span>
              <span className="chip" style={{ marginLeft: "auto" }}>24h</span>
              <span className="chip active">7d</span>
              <span className="chip">30d</span>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {Object.entries(s.byAgent).map(([name, b]) => (
                <div key={name}>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 4 }}>
                    <span className="mono" style={{ fontSize: 13, color: "var(--ink)" }}>{name}</span>
                    <span className="mono tiny dim2">· {Object.keys(b.actions).length} action types</span>
                    <span className="mono" style={{ marginLeft: "auto", color: "var(--ink)" }}>{b.total}</span>
                  </div>
                  {/* stacked bar: ok / warn / err */}
                  <div style={{ display: "flex", height: 10, borderRadius: 2, overflow: "hidden", background: "var(--bg-3)" }}>
                    <div title={`ok ${b.ok}`} style={{ width: `${(b.total / maxAgent) * (b.ok / b.total) * 100}%`, background: "var(--ok)" }} />
                    <div title={`warn ${b.warn}`} style={{ width: `${(b.total / maxAgent) * (b.warn / b.total) * 100}%`, background: "var(--warn)" }} />
                    <div title={`err ${b.error}`} style={{ width: `${(b.total / maxAgent) * (b.error / b.total) * 100}%`, background: "var(--err)" }} />
                  </div>
                  <div className="mono tiny dim2" style={{ marginTop: 3 }}>
                    {b.ok} ok · {b.warn} warn · <span style={{ color: b.error > 0 ? "var(--err)" : "var(--ink-3)" }}>{b.error} err</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* pipeline funnel */}
          <div style={{ background: "var(--bg-1)", border: "1px solid var(--line)", borderRadius: 6, padding: 16 }}>
            <div className="mono" style={{ fontSize: 14, marginBottom: 12 }}>pipeline funnel</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {window.COLUMNS.map(col => {
                const n = s.colCounts[col.id];
                const pct = (n / s.totalActive) * 100;
                return (
                  <div key={col.id}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontFamily: "var(--mono)", fontSize: 11, marginBottom: 3 }}>
                      <span>{col.label}</span>
                      <span className="dim">{n} · wip {col.wip}</span>
                    </div>
                    <div style={{ height: 8, background: "var(--bg-3)", borderRadius: 2, overflow: "hidden" }}>
                      <div style={{ width: `${pct}%`, height: "100%", background: col.id === "replied" ? "var(--ok)" : "var(--ink-2)" }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* throughput sparkline + recent activity */}
        <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 12 }}>
          <div style={{ background: "var(--bg-1)", border: "1px solid var(--line)", borderRadius: 6, padding: 16 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 8 }}>
              <span className="mono" style={{ fontSize: 14 }}>throughput · last 24h</span>
              <span className="mono tiny dim2">tasks per hour</span>
            </div>
            <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 80 }}>
              {s.buckets.map((v, i) => {
                const h = Math.max(2, (v / Math.max(...s.buckets)) * 76);
                return <div key={i} style={{ flex: 1, height: h, background: i === s.buckets.length - 1 ? "var(--ink)" : "var(--ink-3)", borderRadius: 1 }} />;
              })}
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4, fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-4)" }}>
              <span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>now</span>
            </div>
          </div>
          <div style={{ background: "var(--bg-1)", border: "1px solid var(--line)", borderRadius: 6, padding: 16 }}>
            <div className="mono" style={{ fontSize: 14, marginBottom: 8 }}>recent errors</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {window.LOGS.filter(l => l.status === "error").slice(0, 5).map(l => (
                <div key={l.id} className="mono tiny" style={{ display: "flex", gap: 8, padding: "6px 8px", border: "1px dashed var(--line-2)", borderRadius: 3 }}>
                  <span className="dim2">{l.ts}</span>
                  <span style={{ color: "var(--err)" }}>{l.agent}</span>
                  <span className="dim">{l.target}</span>
                  <span className="dim2" style={{ marginLeft: "auto", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{l.message}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Variation B: agent-card grid first ───────────────────────────
function SummaryB() {
  const s = aggregate();
  const ICONS = {
    "researcher": "◇", "person-finder": "◯", "contact-finder": "▽",
    "drafter": "▦", "sender": "▶",
  };
  return (
    <div className="desktop-shell">
      <SumNav />
      <div style={{ flex: 1, overflowY: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 20 }}>
        <div>
          <div className="mono" style={{ fontSize: 18 }}>good morning</div>
          <div className="dim small">5 agents ran <span className="mono" style={{ color: "var(--ink)" }}>{s.totalLogs}</span> tasks in the last 24h. <span className="mono" style={{ color: "var(--err)" }}>{s.errRate}%</span> errored. <span className="mono" style={{ color: "var(--ink)" }}>{s.pendingDrafts}</span> drafts waiting for you.</div>
        </div>

        {/* agent cards */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 12 }}>
          {Object.entries(s.byAgent).map(([name, b]) => {
            const top = Object.entries(b.actions).sort((a, c) => c[1] - a[1])[0];
            return (
              <div key={name} style={{ background: "var(--bg-1)", border: "1px solid var(--line)", borderRadius: 6, padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="mono" style={{ fontSize: 18, color: "var(--ink-3)" }}>{ICONS[name]}</span>
                  <span className="mono" style={{ fontSize: 13 }}>{name}</span>
                </div>
                <div className="mono" style={{ fontSize: 32, lineHeight: 1, color: "var(--ink)" }}>{b.total}</div>
                <div className="mono tiny dim2">tasks (24h)</div>
                <div style={{ display: "flex", height: 4, borderRadius: 2, overflow: "hidden", background: "var(--bg-3)", marginTop: 4 }}>
                  <div style={{ width: `${(b.ok / b.total) * 100}%`, background: "var(--ok)" }} />
                  <div style={{ width: `${(b.warn / b.total) * 100}%`, background: "var(--warn)" }} />
                  <div style={{ width: `${(b.error / b.total) * 100}%`, background: "var(--err)" }} />
                </div>
                <div className="mono tiny" style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: "var(--ok)" }}>{b.ok}</span>
                  <span style={{ color: "var(--warn)" }}>{b.warn}</span>
                  <span style={{ color: "var(--err)" }}>{b.error}</span>
                </div>
                {top && (
                  <div className="mono tiny dim" style={{ borderTop: "1px dashed var(--line)", paddingTop: 6, marginTop: 4 }}>
                    top · <span style={{ color: "var(--ink-2)" }}>{top[0]}</span> ({top[1]})
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* split: pipeline summary + outcomes */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div style={{ background: "var(--bg-1)", border: "1px solid var(--line)", borderRadius: 6, padding: 16 }}>
            <div className="mono" style={{ fontSize: 14, marginBottom: 10 }}>pipeline at a glance</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 6 }}>
              {window.COLUMNS.map(col => (
                <div key={col.id} style={{ background: "var(--bg-2)", border: "1px solid var(--line-2)", borderRadius: 4, padding: 10, textAlign: "center" }}>
                  <div className="mono" style={{ fontSize: 22, color: "var(--ink)" }}>{s.colCounts[col.id]}</div>
                  <div className="mono tiny dim2" style={{ marginTop: 4, lineHeight: 1.2 }}>{col.label}</div>
                </div>
              ))}
            </div>
            <div className="mono tiny dim" style={{ marginTop: 12, display: "flex", gap: 14 }}>
              <span>· total active <span style={{ color: "var(--ink)" }}>{s.totalActive}</span></span>
              <span>· stale <span style={{ color: "var(--warn)" }}>{s.totalStale}</span></span>
              <span>· reply rate <span style={{ color: "var(--ok)" }}>{Math.round((s.colCounts.replied / s.colCounts.sent) * 100)}%</span></span>
            </div>
          </div>

          <div style={{ background: "var(--bg-1)", border: "1px solid var(--line)", borderRadius: 6, padding: 16 }}>
            <div className="mono" style={{ fontSize: 14, marginBottom: 10 }}>tier mix</div>
            <div style={{ display: "flex", gap: 10 }}>
              {[
                { l: "T1", n: s.t1, c: "var(--tier-1)" },
                { l: "T2", n: s.t2, c: "var(--tier-2)" },
                { l: "T3", n: s.t3, c: "var(--tier-3)" },
              ].map((t) => (
                <div key={t.l} style={{ flex: 1, textAlign: "center", padding: 14, border: `1px solid ${t.c}`, borderRadius: 4, color: t.c }}>
                  <div className="mono" style={{ fontSize: 24 }}>{t.n}</div>
                  <div className="mono tiny" style={{ marginTop: 2 }}>{t.l}</div>
                </div>
              ))}
            </div>
            <div className="mono" style={{ fontSize: 14, margin: "16px 0 8px" }}>this week</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <div className="mono small" style={{ display: "flex", justifyContent: "space-between" }}>
                <span className="dim">drafts generated</span><span>{s.pendingDrafts + 22}</span>
              </div>
              <div className="mono small" style={{ display: "flex", justifyContent: "space-between" }}>
                <span className="dim">drafts approved</span><span>22</span>
              </div>
              <div className="mono small" style={{ display: "flex", justifyContent: "space-between" }}>
                <span className="dim">emails sent</span><span>{s.colCounts.sent}</span>
              </div>
              <div className="mono small" style={{ display: "flex", justifyContent: "space-between" }}>
                <span className="dim">replies received</span><span style={{ color: "var(--ok)" }}>{s.colCounts.replied}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Variation C: compact ops report ──────────────────────────────
function SummaryC() {
  const s = aggregate();
  return (
    <div className="desktop-shell">
      <SumNav />
      <div style={{ flex: 1, overflowY: "auto", padding: 24, display: "flex", flexDirection: "column", gap: 20, fontFamily: "var(--mono)" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, borderBottom: "1px dashed var(--line-2)", paddingBottom: 12 }}>
          <span style={{ fontSize: 18 }}>outreach.report</span>
          <span className="dim small">// generated 2026-04-29 · last 24h window</span>
          <span className="chip" style={{ marginLeft: "auto" }}>export csv</span>
        </div>

        {/* big number row */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 0, borderTop: "1px solid var(--line)", borderBottom: "1px solid var(--line)" }}>
          {[
            ["TASKS", s.totalLogs, "total"],
            ["OK", `${s.okRate}%`, "ok rate"],
            ["WARN", s.warnRate + "%", null],
            ["ERR", s.errRate + "%", null],
            ["ACTIVE", s.totalActive, "outreach"],
            ["DRAFTS", s.pendingDrafts, "queued for you"],
          ].map(([l, v, sub], i, arr) => (
            <div key={l} style={{ padding: "14px 16px", borderRight: i < arr.length - 1 ? "1px solid var(--line)" : "none" }}>
              <div className="upper tiny dim2">{l}</div>
              <div style={{ fontSize: 24, color: l === "ERR" ? "var(--err)" : l === "WARN" ? "var(--warn)" : "var(--ink)" }}>{v}</div>
              {sub && <div className="tiny dim">{sub}</div>}
            </div>
          ))}
        </div>

        {/* agent table — terminal style */}
        <div>
          <div className="upper tiny dim2" style={{ marginBottom: 6 }}>tasks_by_agent</div>
          <div style={{ border: "1px solid var(--line)", borderRadius: 4, overflow: "hidden" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1.4fr 80px 80px 80px 80px 2fr", padding: "8px 14px", borderBottom: "1px solid var(--line)", background: "var(--bg-1)", fontSize: 11, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
              <div>agent</div><div>total</div><div>ok</div><div>warn</div><div>err</div><div>top action</div>
            </div>
            {Object.entries(s.byAgent).map(([name, b], i) => {
              const top = Object.entries(b.actions).sort((a, c) => c[1] - a[1])[0];
              return (
                <div key={name} style={{ display: "grid", gridTemplateColumns: "1.4fr 80px 80px 80px 80px 2fr", padding: "10px 14px", borderBottom: i < 4 ? "1px dashed var(--line)" : "none", fontSize: 12, alignItems: "center" }}>
                  <div style={{ color: "var(--ink)" }}>{name}</div>
                  <div>{b.total}</div>
                  <div style={{ color: "var(--ok)" }}>{b.ok}</div>
                  <div style={{ color: "var(--warn)" }}>{b.warn}</div>
                  <div style={{ color: b.error > 0 ? "var(--err)" : "var(--ink-3)" }}>{b.error}</div>
                  <div className="dim">{top ? `${top[0]} (${top[1]})` : "—"}</div>
                </div>
              );
            })}
            <div style={{ display: "grid", gridTemplateColumns: "1.4fr 80px 80px 80px 80px 2fr", padding: "10px 14px", borderTop: "1px solid var(--line)", fontSize: 12, background: "var(--bg-1)" }}>
              <div className="dim">TOTAL</div>
              <div style={{ color: "var(--ink)" }}>{s.totalLogs}</div>
              <div style={{ color: "var(--ok)" }}>{Object.values(s.byAgent).reduce((a, b) => a + b.ok, 0)}</div>
              <div style={{ color: "var(--warn)" }}>{Object.values(s.byAgent).reduce((a, b) => a + b.warn, 0)}</div>
              <div style={{ color: "var(--err)" }}>{Object.values(s.byAgent).reduce((a, b) => a + b.error, 0)}</div>
              <div></div>
            </div>
          </div>
        </div>

        {/* pipeline counts inline */}
        <div>
          <div className="upper tiny dim2" style={{ marginBottom: 6 }}>pipeline_counts</div>
          <div style={{ display: "flex", gap: 0, border: "1px solid var(--line)", borderRadius: 4, overflow: "hidden" }}>
            {window.COLUMNS.map((col, i, arr) => (
              <div key={col.id} style={{ flex: 1, padding: "10px 12px", borderRight: i < arr.length - 1 ? "1px solid var(--line)" : "none", background: col.id === "replied" ? "color-mix(in oklch, var(--ok) 12%, var(--bg-1))" : "var(--bg-1)" }}>
                <div className="tiny dim">{col.label}</div>
                <div style={{ fontSize: 18, marginTop: 2, color: "var(--ink)" }}>{s.colCounts[col.id]}</div>
                <div className="tiny dim2">/ {col.wip} wip</div>
              </div>
            ))}
          </div>
        </div>

        <div className="dim tiny">// reads from outreach/data/tracker.csv → sqlite mirror · agent_logs joined on outreach_id</div>
      </div>
    </div>
  );
}

Object.assign(window, { SummaryA, SummaryB, SummaryC });
