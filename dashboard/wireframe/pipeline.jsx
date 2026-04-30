/* eslint-disable */
// Shared bits for desktop pages

const TopNav = ({ active }) => (
  <div className="topnav">
    <div className="brand mono">~/outreach</div>
    <div className="links">
      <a className={active === "pipeline" ? "active" : ""}>pipeline</a>
      <a className={active === "drafts" ? "active" : ""}>drafts</a>
      <a className={active === "activity" ? "active" : ""}>activity</a>
    </div>
    <div className="right mono">localhost:3000</div>
  </div>
);

const TierBadge = ({ tier }) => (
  <span className={`tier t${tier}`}>T{tier}</span>
);

// ── Pipeline variation A: classic kanban with drawer ────────────────
function PipelineA() {
  const [filter, setFilter] = React.useState("all");
  const [search, setSearch] = React.useState("");
  const [drawerCard, setDrawerCard] = React.useState(null);

  const filterCards = (cards) => cards.filter((c) => {
    if (filter !== "all" && c.tier !== Number(filter)) return false;
    if (search && !(`${c.company} ${c.role} ${c.person}`).toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", position: "relative" }}>
      <TopNav active="pipeline" />
      <div className="kanban-toolbar">
        <div className="search">
          <span className="mono dim2">⌕</span>
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="search company / role / person" />
        </div>
        <div className="chips">
          {["all", "1", "2", "3"].map((t) => (
            <span key={t} className={`chip ${filter === t ? "active" : ""}`} onClick={() => setFilter(t)}>
              {t === "all" ? "all tiers" : `T${t}`}
            </span>
          ))}
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
          <span className="mono tiny dim2">stale ≥ 5d</span>
          <span style={{ width: 8, height: 8, borderLeft: "2px solid var(--warn)", display: "inline-block", height: 12 }} />
          <button className="btn">refresh</button>
        </div>
      </div>
      <div className="kanban-board">
        {window.COLUMNS.map((col) => {
          const cards = filterCards(window.PIPELINE[col.id]);
          return (
            <div className="col" key={col.id}>
              <div className="col-head">
                <span className="name">{col.label}</span>
                <span className="count">{cards.length}/{window.PIPELINE[col.id].length}</span>
                <span className="wip">·wip {col.wip}</span>
              </div>
              <div className="col-body">
                {cards.map((c) => (
                  <div key={c.id} className={`card ${c.stale ? "stale" : ""}`} onClick={() => setDrawerCard(c)}>
                    <div className="row1">
                      <span className="company">{c.company}</span>
                      <TierBadge tier={c.tier} />
                    </div>
                    <div className="role">{c.role}</div>
                    <div className="meta">
                      <span className="person">@ {c.person}</span>
                      <span className="ts" style={{ marginLeft: "auto" }}>{c.lastAction}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>

      {/* drawer */}
      <div className={`drawer-scrim ${drawerCard ? "open" : ""}`} onClick={() => setDrawerCard(null)} />
      <div className={`drawer ${drawerCard ? "open" : ""}`}>
        {drawerCard && (
          <>
            <div className="drawer-head">
              <div>
                <div className="mono small dim">{drawerCard.id}</div>
                <div style={{ fontSize: 16, marginTop: 2 }}>{drawerCard.company} · <span className="dim">{drawerCard.role}</span></div>
              </div>
              <button className="btn" style={{ marginLeft: "auto" }} onClick={() => setDrawerCard(null)}>esc</button>
            </div>
            <div className="drawer-body">
              <div className="drawer-row"><div className="k">person</div><div className="v">{drawerCard.person}</div></div>
              <div className="drawer-row"><div className="k">tier</div><div className="v"><TierBadge tier={drawerCard.tier} /></div></div>
              <div className="drawer-row"><div className="k">column</div><div className="v mono">{drawerCard.column}</div></div>
              <div className="drawer-row"><div className="k">last action</div><div className="v mono">{drawerCard.lastAction}</div></div>
              <div className="drawer-row"><div className="k">stale</div><div className="v">{drawerCard.stale ? <span style={{ color: "var(--warn)" }}>yes — {drawerCard.days}d in column</span> : <span className="dim">no</span>}</div></div>

              <div style={{ marginTop: 18 }} className="upper tiny dim">timeline</div>
              <div className="timeline">
                {window.COLUMNS.map((col) => {
                  const idx = window.COLUMNS.findIndex(c => c.id === drawerCard.column);
                  const cidx = window.COLUMNS.findIndex(c => c.id === col.id);
                  const klass = cidx < idx ? "done" : cidx === idx ? "current" : "todo";
                  return (
                    <div className={`tl-step ${klass}`} key={col.id}>
                      <span style={{ width: 14, color: "var(--ink-4)" }}>{cidx < idx ? "✓" : cidx === idx ? "▸" : "·"}</span>
                      <span>{col.label}</span>
                    </div>
                  );
                })}
              </div>

              <div style={{ marginTop: 18 }} className="upper tiny dim">draft preview</div>
              <div className="dashed" style={{ padding: 12, marginTop: 6, fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-3)" }}>
                {drawerCard.column === "drafted" || drawerCard.column === "queued" || drawerCard.column === "sent" || drawerCard.column === "replied"
                  ? `Hey ${drawerCard.person.split(" ")[0]},\n\nSaw your team's recent work — would love to chat about the ${drawerCard.role} role…`
                  : "[ no draft yet ]"}
              </div>

              <div style={{ display: "flex", gap: 6, marginTop: 16 }}>
                <button className="btn">open in editor</button>
                <button className="btn">view CSV row</button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── Pipeline variation B: density / compact ────────────────────────
function PipelineB() {
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <TopNav active="pipeline" />
      <div className="kanban-toolbar">
        <div className="search"><span className="mono dim2">⌕</span><input placeholder="search…" /></div>
        <div className="chips">
          <span className="chip active">all tiers</span>
          <span className="chip">T1</span>
          <span className="chip">T2</span>
          <span className="chip">T3</span>
        </div>
        <div style={{ marginLeft: "auto", fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-3)" }}>
          204 active · 18 replied · 12 stale
        </div>
      </div>
      <div className="kanban-board">
        {window.COLUMNS.map((col) => (
          <div className="col" key={col.id}>
            <div className="col-head">
              <span className="name">{col.label}</span>
              <span className="count">{window.PIPELINE[col.id].length}</span>
              <span className="wip">·{col.wip}</span>
            </div>
            <div className="col-body">
              {window.PIPELINE[col.id].map((c) => (
                <div key={c.id} className={`card compact ${c.stale ? "stale" : ""}`}>
                  <div className="row1">
                    <span className="company">{c.company}</span>
                    <TierBadge tier={c.tier} />
                    <span className="ts" style={{ marginLeft: "auto" }}>{c.lastAction}</span>
                  </div>
                  <div className="role">{c.role} <span className="dim2">· {c.person}</span></div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Pipeline variation C: funnel-style ─────────────────────────────
function PipelineC() {
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <TopNav active="pipeline" />
      <div className="kanban-toolbar">
        <div className="mono small dim">funnel view · grouped by tier</div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <span className="chip active">funnel</span>
          <span className="chip">kanban</span>
          <span className="chip">table</span>
        </div>
      </div>
      <div style={{ flex: 1, padding: 16, overflowY: "auto", display: "flex", flexDirection: "column", gap: 18 }}>
        {window.COLUMNS.map((col, ci) => {
          const cards = window.PIPELINE[col.id];
          const t1 = cards.filter(c => c.tier === 1).length;
          const t2 = cards.filter(c => c.tier === 2).length;
          const t3 = cards.filter(c => c.tier === 3).length;
          const max = 50;
          const w = (n) => `${(n / max) * 100}%`;
          return (
            <div key={col.id}>
              <div style={{ display: "flex", alignItems: "baseline", marginBottom: 6, gap: 10 }}>
                <span className="mono" style={{ fontSize: 13 }}>{col.label}</span>
                <span className="mono small dim">{cards.length} · wip {col.wip}</span>
                <span className="mono tiny dim2" style={{ marginLeft: "auto" }}>
                  T1 {t1} · T2 {t2} · T3 {t3}
                </span>
              </div>
              <div style={{ display: "flex", height: 28, borderRadius: 4, overflow: "hidden", border: "1px solid var(--line)" }}>
                <div style={{ width: w(t1), background: "color-mix(in oklch, var(--tier-1) 30%, var(--bg-2))", display: "flex", alignItems: "center", paddingLeft: 8, fontFamily: "var(--mono)", fontSize: 11, color: "var(--tier-1)" }}>{t1 > 0 && `T1 · ${t1}`}</div>
                <div style={{ width: w(t2), background: "color-mix(in oklch, var(--tier-2) 30%, var(--bg-2))", display: "flex", alignItems: "center", paddingLeft: 8, fontFamily: "var(--mono)", fontSize: 11, color: "var(--tier-2)" }}>{t2 > 0 && `T2 · ${t2}`}</div>
                <div style={{ width: w(t3), background: "color-mix(in oklch, var(--tier-3) 30%, var(--bg-2))", display: "flex", alignItems: "center", paddingLeft: 8, fontFamily: "var(--mono)", fontSize: 11, color: "var(--tier-3)" }}>{t3 > 0 && `T3 · ${t3}`}</div>
                <div style={{ flex: 1, background: "var(--bg-1)" }} />
              </div>
              <div style={{ display: "flex", gap: 6, marginTop: 6, flexWrap: "wrap" }}>
                {cards.slice(0, 8).map(c => (
                  <span key={c.id} style={{ fontSize: 10, fontFamily: "var(--mono)", padding: "2px 6px", border: "1px solid var(--line)", borderRadius: 3, color: c.stale ? "var(--warn)" : "var(--ink-2)" }}>
                    {c.company}
                  </span>
                ))}
                {cards.length > 8 && <span className="mono tiny dim2">+{cards.length - 8} more</span>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

Object.assign(window, { PipelineA, PipelineB, PipelineC, TopNav, TierBadge });
