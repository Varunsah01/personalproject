/* eslint-disable */
// Agent activity — three variations

function ActivityToolbar({ paused, setPaused, agent, setAgent, statusF, setStatusF, q, setQ }) {
  return (
    <div className="act-toolbar">
      <button className="btn pause-btn" onClick={() => setPaused(!paused)}>
        <span className="dot" style={{ background: paused ? "var(--warn)" : "var(--ok)" }} />
        {paused ? "paused — resume" : "live — pause"}
      </button>
      <div className="search" style={{ display: "flex", alignItems: "center", gap: 6, background: "var(--bg-1)", border: "1px solid var(--line-2)", borderRadius: 4, padding: "4px 8px", flex: "0 1 240px" }}>
        <span className="dim2">⌕</span>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="search message / target" style={{ background: "transparent", border: "none", outline: "none", color: "var(--ink)", fontFamily: "var(--mono)", fontSize: 12, width: "100%" }} />
      </div>
      <div className="chips" style={{ display: "flex", gap: 6 }}>
        <span className={`chip ${agent === "all" ? "active" : ""}`} onClick={() => setAgent("all")}>all agents</span>
        {window.AGENTS.map((a) => (
          <span key={a} className={`chip ${agent === a ? "active" : ""}`} onClick={() => setAgent(a)}>{a}</span>
        ))}
      </div>
      <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
        {["all", "ok", "warn", "error"].map((s) => (
          <span key={s} className={`chip ${statusF === s ? "active" : ""}`} onClick={() => setStatusF(s)}>
            {s !== "all" && <span className={`dot ${s === "error" ? "err" : s}`} style={{ marginRight: 4 }} />}
            {s}
          </span>
        ))}
        <span className="chip">last 1h ▾</span>
      </div>
    </div>
  );
}

function useFiltered() {
  const [paused, setPaused] = React.useState(false);
  const [agent, setAgent] = React.useState("all");
  const [statusF, setStatusF] = React.useState("all");
  const [q, setQ] = React.useState("");
  const filtered = window.LOGS.filter((l) => {
    if (agent !== "all" && l.agent !== agent) return false;
    if (statusF !== "all" && l.status !== statusF) return false;
    if (q && !(`${l.message} ${l.target} ${l.action}`).toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  });
  return { paused, setPaused, agent, setAgent, statusF, setStatusF, q, setQ, filtered };
}

// ── Variation A: structured table ───────────────────────────────
function ActivityA() {
  const f = useFiltered();
  return (
    <div className="act-shell">
      <div className="topnav">
        <div className="brand mono">~/outreach</div>
        <div className="links">
          <a>pipeline</a><a>drafts</a><a className="active">activity</a>
        </div>
        <div className="right mono">{f.filtered.length} rows · localhost:3000</div>
      </div>
      <ActivityToolbar {...f} />
      <div className="act-body">
        <div className="log-list">
          <div className="log-row" style={{ borderBottom: "1px solid var(--line)", color: "var(--ink-3)", textTransform: "uppercase", fontSize: 10, letterSpacing: "0.06em" }}>
            <div>ts</div><div>agent</div><div>action</div><div>target · message</div><div>status</div>
          </div>
          {f.filtered.slice(0, 200).map((l) => (
            <div key={l.id} className={`log-row ${l.status === "error" ? "error" : l.status === "warn" ? "warn" : ""}`}>
              <div className="ts">{l.ts}</div>
              <div className="agent">{l.agent}</div>
              <div className="action">{l.action}</div>
              <div className="target">
                <span style={{ color: "var(--ink-2)" }}>{l.target}</span>
                <span className="dim2"> · {l.message}</span>
              </div>
              <div className="status-cell">
                <span className={`dot ${l.status === "error" ? "err" : l.status}`} />
                <span className={l.status === "error" ? "" : ""} style={{ color: l.status === "error" ? "var(--err)" : l.status === "warn" ? "var(--warn)" : "var(--ok)" }}>{l.status}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Variation B: terminal-style tail ────────────────────────────
function ActivityB() {
  const f = useFiltered();
  const counts = {};
  window.LOGS.forEach(l => counts[l.agent] = (counts[l.agent] || 0) + 1);
  return (
    <div className="act-shell">
      <div className="topnav">
        <div className="brand mono">~/outreach</div>
        <div className="links">
          <a>pipeline</a><a>drafts</a><a className="active">activity</a>
        </div>
        <div className="right mono">tail -f agent_logs</div>
      </div>
      <ActivityToolbar {...f} />
      <div className="act-body">
        <div className="act-sidebar">
          <div className="upper tiny dim2" style={{ padding: "4px 8px" }}>agents</div>
          <div className={`item ${f.agent === "all" ? "active" : ""}`} onClick={() => f.setAgent("all")}>
            <span>all</span><span className="n">{window.LOGS.length}</span>
          </div>
          {window.AGENTS.map((a) => (
            <div key={a} className={`item ${f.agent === a ? "active" : ""}`} onClick={() => f.setAgent(a)}>
              <span>{a}</span><span className="n">{counts[a] || 0}</span>
            </div>
          ))}
          <div style={{ height: 16 }} />
          <div className="upper tiny dim2" style={{ padding: "4px 8px" }}>status</div>
          {["ok", "warn", "error"].map((s) => (
            <div key={s} className={`item ${f.statusF === s ? "active" : ""}`} onClick={() => f.setStatusF(s)}>
              <span><span className={`dot ${s === "error" ? "err" : s}`} style={{ marginRight: 6 }} />{s}</span>
              <span className="n">{window.LOGS.filter(l => l.status === s).length}</span>
            </div>
          ))}
        </div>
        <div className="terminal">
          {f.filtered.slice(0, 240).map((l) => (
            <div key={l.id} className="line">
              <span className="ts">{l.ts}.{l.ms}</span>{"  "}
              <span className="agent">[{l.agent.padEnd(15)}]</span>{" "}
              <span className="action">{l.action.padEnd(20)}</span>{" "}
              <span className="dim">{l.target.padEnd(15)}</span>{" "}
              <span className={l.status}>{l.status.toUpperCase().padEnd(5)}</span>{" "}
              <span className="dim2">{l.message}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Variation C: timeline grouped by run ────────────────────────
function ActivityC() {
  const f = useFiltered();
  // group consecutive logs into "runs" — by target swap
  const runs = [];
  let cur = null;
  f.filtered.forEach((l) => {
    if (!cur || cur.target !== l.target || cur.lines.length > 6) {
      cur = { target: l.target, ts: l.ts, lines: [] };
      runs.push(cur);
    }
    cur.lines.push(l);
  });
  return (
    <div className="act-shell">
      <div className="topnav">
        <div className="brand mono">~/outreach</div>
        <div className="links">
          <a>pipeline</a><a>drafts</a><a className="active">activity</a>
        </div>
        <div className="right mono">grouped by target</div>
      </div>
      <ActivityToolbar {...f} />
      <div style={{ flex: 1, overflowY: "auto" }}>
        {runs.slice(0, 40).map((r, i) => {
          const errs = r.lines.filter(l => l.status === "error").length;
          const warns = r.lines.filter(l => l.status === "warn").length;
          return (
            <div key={i} className="timeline-run">
              <div className="run-head">
                <span className="dim">{r.ts}</span>
                <span style={{ color: "var(--ink)" }}>{r.target}</span>
                <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
                  {errs > 0 && <span style={{ color: "var(--err)" }}>{errs} err</span>}
                  {warns > 0 && <span style={{ color: "var(--warn)" }}>{warns} warn</span>}
                  <span className="dim2">{r.lines.length} steps</span>
                </span>
              </div>
              <div className="run-body">
                {r.lines.map((l) => (
                  <div key={l.id} className="log-row" style={{ gridTemplateColumns: "70px 100px 130px 1fr 70px" }}>
                    <div className="ts">{l.ts}</div>
                    <div className="agent">{l.agent}</div>
                    <div className="action">{l.action}</div>
                    <div className="target dim2">{l.message}</div>
                    <div className="status-cell">
                      <span className={`dot ${l.status === "error" ? "err" : l.status}`} />
                      <span style={{ color: l.status === "error" ? "var(--err)" : l.status === "warn" ? "var(--warn)" : "var(--ok)" }}>{l.status}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
        <div style={{ height: 30 }} />
      </div>
    </div>
  );
}

Object.assign(window, { ActivityA, ActivityB, ActivityC });
