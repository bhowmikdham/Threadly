// Phase 6 edge states for the side panel:
// - Tier 1 learning
// - Overdue commitments
// - Search results
// - Empty digest
// - Error / offline

const { useState: useS_ed, useMemo: useM_ed } = React;

// Pull shared widgets off window (defined in sidepanel.jsx)
const DebtCard = window.DebtCard;
const CommitmentSection = window.CommitmentSection;

// ---------- Tier 1 learning state ----------
function Tier1State({ data }) {
  return (
    <div className="thin-scroll" style={{ flex: 1, overflowY: "auto", padding: "14px 14px 20px" }}>
      <Card padding={14} style={{ background: "var(--accent-soft)", borderColor: "var(--accent-line)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
          <span style={{
            display: "inline-grid", placeItems: "center",
            width: 28, height: 28, borderRadius: 6,
            background: "var(--accent)", color: "var(--accent-ink)",
          }}>{Icons.sparkle}</span>
          <div style={{ flex: 1 }}>
            <div className="display" style={{ fontSize: 14, fontWeight: 500 }}>Learning your voice — tier 1 of 3</div>
            <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)", marginTop: 2 }}>read 14 messages so far · 36 more to unlock fine-tuning</div>
          </div>
        </div>
        <div style={{
          height: 5, borderRadius: 3, background: "rgba(0,0,0,0.06)", overflow: "hidden",
        }}>
          <div style={{ width: "28%", height: "100%", background: "var(--accent)" }} />
        </div>
        <div style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 10, lineHeight: 1.5 }}>
          Drafts come from voice retrieval over your Sent folder for now. They'll feel more native after fine-tuning.
        </div>
      </Card>

      <div style={{ height: 14 }} />
      <DebtCard data={data.debt} />
      <div style={{ height: 14 }} />
      <CommitmentSection commitments={data.commitments.slice(0,3)} compact />
    </div>
  );
}

// ---------- Overdue commitments state ----------
function OverdueState({ data, onDraft }) {
  const overdue = data.commitments.filter(c => c.status === "overdue");
  const pending = data.commitments.filter(c => c.status !== "overdue");
  return (
    <div className="thin-scroll" style={{ flex: 1, overflowY: "auto", padding: "14px 14px 20px" }}>
      <Card padding={14} style={{ background: "var(--warning-soft)", borderColor: "color-mix(in oklab, var(--warning) 30%, transparent)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
          <span style={{ color: "var(--warning)" }}>{Icons.warning}</span>
          <div className="display" style={{ fontSize: 14, fontWeight: 500 }}>1 overdue commitment</div>
        </div>
        <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginBottom: 12 }}>
          Helix Health is still waiting on you. Tap to draft a quick reply or snooze with a reason.
        </div>
        {overdue.map(c => (
          <div key={c.id} style={{
            background: "var(--surface)", borderRadius: 6, padding: 10,
            display: "flex", alignItems: "flex-start", gap: 10,
            border: "1px solid var(--line)",
          }}>
            <span style={{
              width: 16, height: 16, borderRadius: 999, marginTop: 1, flexShrink: 0,
              border: "1.5px solid var(--warning)", background: "color-mix(in oklab, var(--warning) 25%, transparent)",
            }} />
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 500 }}>{c.text}</div>
              <div className="mono" style={{ fontSize: 10.5, color: "var(--warning)", marginTop: 2 }}>
                overdue · was due {c.due}
              </div>
            </div>
          </div>
        ))}
        <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
          <Button kind="primary" size="sm" icon={Icons.edit} style={{ flex: 1 }} onClick={onDraft}>Draft quick reply</Button>
          <Button kind="secondary" size="sm" icon={Icons.clock}>Snooze</Button>
        </div>
      </Card>

      <div style={{ height: 14 }} />
      <SectionLabel right={`${pending.length} more pending`}>Still open</SectionLabel>
      <CommitmentSection commitments={pending} />
    </div>
  );
}

// ---------- Search results ----------
function SearchState({ data }) {
  const [query, setQuery] = useS_ed("emails from priya about pricing");

  const results = useM_ed(() => [
    { from: "Priya Ramaswamy", subj: "Q3 OKR review — pricing experiment results", snip: "...conversion lifted 7.4% but ARPU stayed flat. I think we should hold the rollout...", date: "today", highlights: ["priya", "pricing"] },
    { from: "Priya Ramaswamy", subj: "Pricing experiment v1 — results", snip: "...the original test on the Tier-1 cohort hit the conversion target but didn't move...", date: "Mar 4", highlights: ["priya", "pricing"] },
    { from: "Daniel Okafor", subj: "Q3 OKRs — pricing & growth", snip: "...Priya, looping you in on the pricing OKR for the quarter — we need a clear...", date: "Feb 18", highlights: ["priya", "pricing"] },
    { from: "Priya Ramaswamy", subj: "Re: Pricing dashboard fields", snip: "...thanks — added the discount column and the ARPU split. One question on the...", date: "Jan 28", highlights: ["priya", "pricing"] },
  ], []);

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
      <div style={{ padding: "12px 14px 8px" }}>
        <SectionLabel right={`${results.length} results · 0.4s`}>Semantic search</SectionLabel>
        <div style={{
          background: "var(--surface)", border: "1px solid var(--accent)",
          borderRadius: 6, padding: "8px 10px",
          display: "flex", alignItems: "center", gap: 8,
          boxShadow: "0 0 0 3px color-mix(in oklab, var(--accent) 14%, transparent)",
        }}>
          <span style={{ color: "var(--accent)" }}>{Icons.search}</span>
          <input value={query} onChange={e => setQuery(e.target.value)} style={{
            flex: 1, border: "none", outline: "none", background: "transparent",
            fontSize: 13, color: "var(--ink)", fontFamily: "var(--font-sans)",
          }} />
          <button onClick={() => setQuery("")} style={{
            background: "transparent", border: "none", color: "var(--ink-4)",
            cursor: "pointer", padding: 2, display: "flex",
          }}>{Icons.x}</button>
        </div>
        <div style={{ display: "flex", gap: 5, marginTop: 8, flexWrap: "wrap" }}>
          <Chip tone="outline" active>last 6 months</Chip>
          <Chip tone="outline">Priya Ramaswamy</Chip>
          <Chip tone="outline">has: attachment</Chip>
          <Chip tone="outline">+ filter</Chip>
        </div>
      </div>

      <div className="thin-scroll" style={{ flex: 1, overflowY: "auto", padding: "8px 14px 20px" }}>
        {results.map((r, i) => (
          <div key={i} style={{
            padding: "10px 0",
            borderBottom: i < results.length-1 ? "1px solid var(--line)" : "none",
            cursor: "pointer",
          }}>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8, marginBottom: 2 }}>
              <span style={{ fontSize: 12.5, fontWeight: 500 }}>{r.from}</span>
              <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-4)" }}>{r.date}</span>
            </div>
            <div style={{ fontSize: 13, color: "var(--ink), marginBottom: 3" }}>
              <Highlight text={r.subj} terms={r.highlights} />
            </div>
            <div style={{ fontSize: 12, color: "var(--ink-3)", lineHeight: 1.45 }}>
              <Highlight text={r.snip} terms={r.highlights} />
            </div>
          </div>
        ))}

        <div style={{
          marginTop: 14, padding: 12,
          background: "var(--surface-2)", borderRadius: 6,
          fontSize: 11, color: "var(--ink-4)", lineHeight: 1.5,
          display: "flex", gap: 8, alignItems: "flex-start",
        }}>
          <span style={{ color: "var(--ink-3)", marginTop: 1 }}>{Icons.info}</span>
          <span>Searches your inbox semantically — meaning, not just keywords. The query understood "pricing" includes ARPU, discount, plan, and revenue mentions.</span>
        </div>
      </div>
    </div>
  );
}

function Highlight({ text, terms }) {
  if (!terms?.length) return text;
  const re = new RegExp(`(${terms.join("|")})`, "ig");
  const parts = text.split(re);
  return parts.map((p, i) =>
    re.test(p) ? <mark key={i} style={{ background: "var(--accent-soft)", color: "var(--ink)", padding: "0 2px", borderRadius: 2 }}>{p}</mark> : <React.Fragment key={i}>{p}</React.Fragment>
  );
}

// ---------- Empty digest ----------
function EmptyDigestState() {
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
      <div style={{ padding: "14px 14px 8px" }}>
        <SectionLabel right="Monday, May 13">Weekly digest</SectionLabel>
      </div>
      <div style={{
        flex: 1, display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center",
        padding: "0 28px", textAlign: "center", gap: 14,
      }}>
        <div style={{
          width: 56, height: 56, borderRadius: 999,
          background: "var(--surface-2)", color: "var(--ink-4)",
          display: "grid", placeItems: "center",
        }}>{Icons.calendar}</div>
        <div>
          <div className="display" style={{ fontSize: 18, fontWeight: 500, marginBottom: 4 }}>Quiet week.</div>
          <div style={{ fontSize: 13, color: "var(--ink-3)", lineHeight: 1.5 }}>
            You closed every commitment and replied to everyone who needed a reply. We didn't find anything worth surfacing.
          </div>
        </div>
        <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-4)", letterSpacing: "0.04em" }}>
          Next digest · Mon May 20, 9:00
        </div>
      </div>
      <div style={{ padding: 14, borderTop: "1px solid var(--line)" }}>
        <Button kind="ghost" size="sm" full icon={Icons.clock}>See last 4 digests</Button>
      </div>
    </div>
  );
}

// ---------- Error / offline ----------
function ErrorState({ onRetry }) {
  return (
    <div className="thin-scroll" style={{ flex: 1, overflowY: "auto", padding: "14px 14px 20px" }}>
      <Card padding={14} style={{ background: "var(--danger-soft)", borderColor: "color-mix(in oklab, var(--danger) 30%, transparent)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
          <span style={{ color: "var(--danger)" }}>{Icons.warning}</span>
          <div className="display" style={{ fontSize: 14, fontWeight: 500 }}>Can't reach MailMind backend.</div>
        </div>
        <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginBottom: 12, lineHeight: 1.5 }}>
          We're showing cached commitments from 14 minutes ago. New replies, agent actions, and voice are paused until we reconnect.
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <Button kind="primary" size="sm" icon={Icons.undo} onClick={onRetry}>Retry</Button>
          <Button kind="ghost" size="sm">Status page</Button>
        </div>
      </Card>

      <div style={{ height: 14 }} />
      <Card padding={12} style={{ background: "var(--surface-2)" }}>
        <div className="mono" style={{ fontSize: 10, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--ink-4)", marginBottom: 6 }}>
          Last known
        </div>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>23 unanswered · 6 open commitments · 1 overdue</div>
      </Card>

      <div style={{ height: 14 }} />
      <SectionLabel right="cached">Commitments</SectionLabel>
      <Card padding={0} style={{ opacity: 0.6 }}>
        {[1,2,3].map(i => (
          <div key={i} style={{
            display: "flex", gap: 10, padding: "10px 12px",
            borderBottom: i < 3 ? "1px solid var(--line)" : "none",
          }}>
            <div style={{ width: 16, height: 16, borderRadius: 999, border: "1.5px solid var(--line-strong)", flexShrink: 0, marginTop: 1 }} />
            <div style={{ flex: 1 }}>
              <div className="skeleton" style={{ height: 11, width: "70%", marginBottom: 6 }} />
              <div className="skeleton" style={{ height: 9, width: "45%" }} />
            </div>
          </div>
        ))}
      </Card>
    </div>
  );
}

// ---------- "Action just taken" overlay panel ----------
function ActionTakenState({ data }) {
  return (
    <div className="thin-scroll" style={{ flex: 1, overflowY: "auto", padding: "14px 14px 20px" }}>
      <Card padding={14} style={{ background: "var(--success-soft)", borderColor: "color-mix(in oklab, var(--success) 30%, transparent)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
          <span style={{ color: "var(--success)" }}>{Icons.checkCircle}</span>
          <div className="display" style={{ fontSize: 14, fontWeight: 500 }}>Draft saved to Priya thread</div>
        </div>
        <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginBottom: 10 }}>
          Pulled from your "Brief & direct" variant. Nothing was sent — it's waiting in your drafts for review.
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <Button kind="primary" size="sm" icon={Icons.external}>Open draft</Button>
          <Button kind="ghost" size="sm" icon={Icons.undo}>Undo</Button>
        </div>
      </Card>

      <div style={{ height: 14 }} />
      <SectionLabel right="just now">Recent activity</SectionLabel>
      <Card padding={0}>
        {data.actions.slice(0, 3).map((a, i) => (
          <div key={a.id} style={{
            display: "flex", alignItems: "flex-start", gap: 10,
            padding: "10px 12px",
            borderBottom: i < 2 ? "1px solid var(--line)" : "none",
            animation: i === 0 ? "slideUp var(--dur) var(--ease)" : "none",
            background: i === 0 ? "var(--accent-soft)" : "transparent",
          }}>
            <span style={{
              width: 6, height: 6, borderRadius: 999,
              background: i === 0 ? "var(--accent)" : "var(--ink-4)",
              marginTop: 7, flexShrink: 0,
            }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12.5 }}><span style={{ fontWeight: 500 }}>{a.verb}</span>: {a.target}</div>
              <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-4)", marginTop: 2 }}>{a.time}</div>
            </div>
            {a.reversible && <button style={{ background: "transparent", border: "1px solid var(--line)", color: "var(--ink-2)", padding: "2px 7px", borderRadius: 4, fontSize: 10.5, cursor: "pointer", fontFamily: "var(--font-mono)" }}>undo</button>}
          </div>
        ))}
      </Card>
    </div>
  );
}

Object.assign(window, { Tier1State, OverdueState, SearchState, EmptyDigestState, ErrorState, ActionTakenState });
