/* eslint-disable */
// Drafts — mobile-first, three variations.
// All wrapped in an iOS frame on the canvas.

const { IOSFrame } = window;

function ScoreBar({ score }) {
  return (
    <div className="score-bar">
      <span className="upper tiny">manager_score</span>
      <span className="score-num">{score}</span>
      <div className="bar"><div className="bar-fill" style={{ width: `${score}%` }} /></div>
      <span className="dim2 tiny">/100</span>
    </div>
  );
}

function PriorOutreach({ prior }) {
  if (!prior || prior.length === 0) {
    return <div className="mono tiny dim2">· no prior outreach</div>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      {prior.map((p, i) => (
        <div key={i} className="mono tiny dim">
          · {p.when} · {p.channel} → {p.outcome}
        </div>
      ))}
    </div>
  );
}

// ── Variation A: full body inline, big sticky APPROVE/REJECT ───────
function DraftsA() {
  const [idx, setIdx] = React.useState(0);
  const [rejecting, setRejecting] = React.useState(false);
  const [reason, setReason] = React.useState("");
  const drafts = window.DRAFTS;
  const d = drafts[idx];
  const next = () => setIdx((i) => Math.min(i + 1, drafts.length - 1));

  const REASON_CHIPS = ["wrong-tone", "off-topic", "too-long", "regen", "wrong-person"];

  return (
    <div className="drafts-shell">
      <div className="drafts-top">
        <span className="mono" style={{ fontSize: 13 }}>drafts</span>
        <span className="mono tiny dim2">· awaiting approval</span>
        <span className="count">{idx + 1} / {drafts.length}</span>
      </div>

      <div style={{ flex: 1, overflowY: "auto" }}>
        <div className="draft-card">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="mono" style={{ fontSize: 13 }}>{d.company}</span>
            <span className={`tier t${d.tier}`}>T{d.tier}</span>
            <span className="mono tiny dim2" style={{ marginLeft: "auto" }}>{d.generated}</span>
          </div>
          <div className="dim small">{d.role} · @{d.person}</div>

          <ScoreBar score={d.score} />

          <div className="upper tiny dim" style={{ marginTop: 4 }}>why this person</div>
          <div className="mono tiny dim">· {d.reason}</div>

          <div className="upper tiny dim" style={{ marginTop: 4 }}>prior outreach</div>
          <PriorOutreach prior={d.prior} />

          <div className="upper tiny dim" style={{ marginTop: 4 }}>hook</div>
          <div className="hook-quote">"{d.hook}"</div>

          <div className="upper tiny dim" style={{ marginTop: 4 }}>body</div>
          <div className="body-text">{d.body}</div>

          <div style={{ display: "flex", gap: 8, justifyContent: "space-between" }}>
            <button className="btn">edit</button>
            <button className="btn">regenerate</button>
            <button className="btn">skip ›</button>
          </div>

          <div className="upper tiny dim" style={{ marginTop: 4 }}>send when</div>
          <div style={{ display: "flex", gap: 6 }}>
            {["now", "9:30am tmrw", "tue 8am", "pick…"].map((s, i) => (
              <span key={s} className={`chip ${i === 1 ? "active" : ""}`}>{s}</span>
            ))}
          </div>
        </div>

        {rejecting && (
          <div style={{ padding: 16, borderBottom: "1px solid var(--line)", background: "var(--bg-1)" }}>
            <div className="upper tiny dim" style={{ marginBottom: 6 }}>reject — quick reasons</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
              {REASON_CHIPS.map((r) => (
                <span key={r} className={`chip ${reason === r ? "active" : ""}`} onClick={() => setReason(r)}>{r}</span>
              ))}
            </div>
            <textarea className="field" rows={3} style={{ width: "100%" }} placeholder="optional note…" />
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <button className="btn" onClick={() => { setRejecting(false); setReason(""); }}>cancel</button>
              <button className="btn primary" style={{ marginLeft: "auto" }} onClick={() => { setRejecting(false); setReason(""); next(); }}>confirm reject</button>
            </div>
          </div>
        )}
      </div>

      <div className="actions-sticky">
        <button className="big-btn reject" onClick={() => setRejecting(true)}>REJECT</button>
        <button className="big-btn approve" onClick={next}>APPROVE</button>
      </div>
    </div>
  );
}

// ── Variation B: card-stack / swipeable feel, truncated body ───────
function DraftsB() {
  const [expanded, setExpanded] = React.useState({});
  const drafts = window.DRAFTS;

  return (
    <div className="drafts-shell">
      <div className="drafts-top">
        <span className="mono" style={{ fontSize: 13 }}>drafts</span>
        <span className="count">{drafts.length} pending</span>
      </div>
      <div style={{ flex: 1, overflowY: "auto" }} className="draft-stack">
        {drafts.map((d) => (
          <div key={d.id} className="draft-card">
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span className="mono" style={{ fontSize: 13 }}>{d.company}</span>
              <span className={`tier t${d.tier}`}>T{d.tier}</span>
              <span className="mono tiny" style={{ marginLeft: "auto", color: d.score > 75 ? "var(--ok)" : d.score > 55 ? "var(--ink-2)" : "var(--warn)" }}>
                {d.score}
              </span>
            </div>
            <div className="dim small">{d.role} · @{d.person}</div>
            <div className="hook-quote">"{d.hook}"</div>
            <div className={`body-text ${expanded[d.id] ? "" : "collapsed"}`}>{d.body}</div>
            <button className="btn" style={{ alignSelf: "flex-start" }} onClick={() => setExpanded(e => ({ ...e, [d.id]: !e[d.id] }))}>
              {expanded[d.id] ? "collapse" : "expand"}
            </button>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 4 }}>
              <button className="big-btn reject" style={{ height: 44 }}>REJECT</button>
              <button className="big-btn approve" style={{ height: 44 }}>APPROVE</button>
            </div>
          </div>
        ))}
        <div style={{ height: 60 }} />
      </div>
    </div>
  );
}

// ── Variation C: tinder-y single-card mode (one at a time) ─────────
function DraftsC() {
  const [idx, setIdx] = React.useState(0);
  const drafts = window.DRAFTS;
  const d = drafts[idx];
  const adv = () => setIdx((i) => (i + 1) % drafts.length);

  return (
    <div className="drafts-shell">
      <div className="drafts-top">
        <span className="mono" style={{ fontSize: 13 }}>drafts · single</span>
        <span className="count">{idx + 1} of {drafts.length}</span>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: 12, display: "flex", flexDirection: "column", gap: 12 }}>
        <div className="panel" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <div>
              <div className="mono" style={{ fontSize: 15 }}>{d.company}</div>
              <div className="dim small">{d.role}</div>
            </div>
            <div style={{ textAlign: "right" }}>
              <div className="mono" style={{ fontSize: 22 }}>{d.score}</div>
              <div className="mono tiny dim2">manager_score</div>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className={`tier t${d.tier}`}>T{d.tier}</span>
            <span className="dim small">{d.person}</span>
          </div>
          <div style={{ borderTop: "1px dashed var(--line)", paddingTop: 8 }}>
            <div className="upper tiny dim">why</div>
            <div className="mono tiny dim" style={{ marginTop: 4 }}>· {d.reason}</div>
          </div>
          <div>
            <div className="upper tiny dim">hook</div>
            <div className="hook-quote">"{d.hook}"</div>
          </div>
          <div className="body-text">{d.body}</div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <button className="btn">edit</button>
            <button className="btn">regenerate</button>
            <button className="btn">skip for later</button>
            <button className="btn">prior outreach</button>
          </div>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <span className="mono tiny dim">send</span>
            <span className="chip active">9:30am tmrw</span>
            <span className="chip">change…</span>
          </div>
        </div>
        <div className="dashed" style={{ padding: 10, fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-3)" }}>
          swipe ← reject · → approve · ↑ skip
        </div>
      </div>
      <div className="actions-sticky">
        <button className="big-btn reject" onClick={adv}>REJECT</button>
        <button className="big-btn approve" onClick={adv}>APPROVE</button>
      </div>
    </div>
  );
}

Object.assign(window, { DraftsA, DraftsB, DraftsC });
