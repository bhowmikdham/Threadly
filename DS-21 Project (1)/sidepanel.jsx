// Side Panel — the daily-driver UI at fixed width (400–480px).
// Renders one of several contextual states.

const { useState, useEffect, useRef, useMemo } = React;

// ---------- Header (persistent) ----------
function SidePanelHeader({ tier = 2, onTier, query, setQuery, onSettings }) {
  return (
    <div style={{
      padding: "10px 14px 8px",
      borderBottom: "1px solid var(--line)",
      background: "var(--bg-elev)",
      display: "flex", flexDirection: "column", gap: 8,
      flexShrink: 0,
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{
            width: 22, height: 22, borderRadius: 6,
            background: "var(--ink)", color: "var(--bg)",
            display: "grid", placeItems: "center",
            fontFamily: "var(--font-display)", fontWeight: 500,
            fontStyle: "italic", fontSize: 13, letterSpacing: "-0.02em",
          }}>M</div>
          <div className="display" style={{
            fontSize: 15, fontWeight: 500, letterSpacing: "-0.02em",
          }}>MailMind</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <TierPill tier={tier} total={3} label={tier===3?"Trained":tier===2?"Training":"Learning"} />
          <Button kind="ghost" size="sm" icon={Icons.settings} onClick={onSettings} style={{ padding: 6 }} />
        </div>
      </div>
      <div style={{ position: "relative" }}>
        <div style={{ position: "absolute", left: 8, top: 7, color: "var(--ink-4)" }}>{Icons.search}</div>
        <input
          value={query || ""}
          onChange={e => setQuery && setQuery(e.target.value)}
          placeholder="Search inbox in plain English…"
          style={{
            width: "100%", height: 30, borderRadius: 6,
            background: "var(--surface)",
            border: "1px solid var(--line)",
            padding: "0 8px 0 28px",
            fontSize: 12.5, color: "var(--ink)",
            fontFamily: "var(--font-sans)",
            outline: "none",
          }}
        />
      </div>
    </div>
  );
}

// ---------- Footer (persistent: mic + action log toggle) ----------
function SidePanelFooter({ onMic, micState, onLog, logCount, onCompose }) {
  const breathing = micState === "listening";
  return (
    <div style={{
      padding: "10px 14px",
      borderTop: "1px solid var(--line)",
      background: "var(--bg-elev)",
      display: "flex", alignItems: "center", gap: 10,
      flexShrink: 0,
    }}>
      <button onClick={onLog} style={{
        display: "flex", alignItems: "center", gap: 6,
        padding: "6px 10px", borderRadius: 6,
        background: "transparent", border: "1px solid var(--line)",
        color: "var(--ink-2)", fontSize: 12, cursor: "pointer",
        fontFamily: "var(--font-sans)",
      }}>
        {Icons.clock}<span>Log</span>
        <span className="mono" style={{ color: "var(--ink-4)", fontSize: 11 }}>{logCount}</span>
      </button>
      <div style={{ flex: 1 }} />
      <button onMouseDown={() => onMic && onMic("listen")}
              onMouseUp={() => onMic && onMic("idle")}
              onClick={() => onMic && onMic("toggle")}
              style={{
        position: "relative",
        width: 44, height: 44, borderRadius: 999,
        background: micState === "idle" ? "var(--accent)" : "var(--ink)",
        color: micState === "idle" ? "var(--accent-ink)" : "var(--bg)",
        border: "none", cursor: "pointer",
        display: "grid", placeItems: "center",
        boxShadow: "var(--shadow-md)",
        transition: "background var(--dur) var(--ease), transform var(--dur-fast) var(--ease)",
      }}>
        {breathing && <span style={{
          position: "absolute", inset: -4, borderRadius: 999,
          border: "2px solid var(--accent)", opacity: 0.4,
          animation: "pulse 1.2s infinite var(--ease)",
        }} />}
        {Icons.mic}
      </button>
    </div>
  );
}

// ---------- State: Signed out ----------
function SignedOutState({ onSignIn }) {
  return (
    <div style={{
      flex: 1, display: "flex", flexDirection: "column", justifyContent: "center",
      padding: "0 20px", textAlign: "left", gap: 20,
    }}>
      <div>
        <div className="display" style={{
          fontSize: 28, lineHeight: 1.1, letterSpacing: "-0.02em",
          fontWeight: 400, marginBottom: 10,
        }}>
          A quiet, capable layer for your inbox.
        </div>
        <div style={{ color: "var(--ink-3)", fontSize: 13.5, lineHeight: 1.55 }}>
          MailMind reads alongside you, remembers what you've promised, and writes in your voice. It never sends anything without asking.
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6, color: "var(--ink-2)" }}>
        {[
          ["Tracks commitments across every thread"],
          ["Drafts replies in your voice"],
          ["Triages reply-or-not at a glance"],
        ].map(([t], i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
            <span style={{ color: "var(--accent)" }}>{Icons.check}</span>
            <span>{t}</span>
          </div>
        ))}
      </div>

      <Button kind="primary" size="lg" full onClick={onSignIn}>
        Connect your Gmail
      </Button>
      <div style={{ fontSize: 11, color: "var(--ink-4)", lineHeight: 1.5, textAlign: "center" }}>
        Read & write access via Google OAuth.<br/>You can revoke any time.
      </div>
    </div>
  );
}

// ---------- State: Authenticated, no Gmail tab ----------
function NoGmailTabState({ data, onOpenThread }) {
  return (
    <div className="thin-scroll" style={{ flex: 1, overflowY: "auto", padding: "14px 14px 20px" }}>
      <DebtCard data={data.debt} />
      <div style={{ height: 14 }} />
      <CommitmentSection commitments={data.commitments.slice(0,3)} compact />
      <div style={{ height: 14 }} />
      <RecentInboxSection inbox={data.inbox.slice(0,4)} onOpen={onOpenThread} />
      <div style={{ height: 14 }} />
      <DigestCTA />
    </div>
  );
}

// ---------- State: Inbox context (Gmail visible, no thread open) ----------
function InboxContextState({ data }) {
  const counts = useMemo(() => {
    const c = { Action: 0, Ask: 0, FYI: 0, Social: 0 };
    data.inbox.forEach(r => { c[r.label] = (c[r.label]||0) + 1; });
    return c;
  }, [data.inbox]);
  return (
    <div className="thin-scroll" style={{ flex: 1, overflowY: "auto", padding: "14px 14px 20px" }}>
      <DebtCard data={data.debt} />
      <div style={{ height: 14 }} />

      <SectionLabel right="auto-labels">Reply triage</SectionLabel>
      <Card padding={12}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 8 }}>
          <TriageCell label="Action"  value={counts.Action} tone="warm" />
          <TriageCell label="Ask"     value={counts.Ask}    tone="warning" />
          <TriageCell label="FYI"     value={counts.FYI}    tone="cool" />
          <TriageCell label="Social"  value={counts.Social} tone="muted" />
        </div>
        <div style={{ height: 10 }} />
        <div style={{
          display: "flex", height: 6, borderRadius: 3, overflow: "hidden",
          background: "var(--surface-2)",
        }}>
          <span style={{ flex: counts.Action, background: "var(--reply-yes)" }} />
          <span style={{ flex: counts.Ask, background: "var(--warning)" }} />
          <span style={{ flex: counts.FYI, background: "var(--reply-fyi)" }} />
          <span style={{ flex: counts.Social, background: "var(--reply-news)" }} />
        </div>
        <div style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 8, fontFamily: "var(--font-mono)" }}>
          {data.inbox.length} unread · auto-labeled this morning
        </div>
      </Card>

      <div style={{ height: 14 }} />
      <CommitmentSection commitments={data.commitments} />
    </div>
  );
}

function TriageCell({ label, value, tone }) {
  const dot = {
    warm: "var(--reply-yes)", cool: "var(--reply-fyi)",
    muted: "var(--reply-news)", warning: "var(--warning)",
  }[tone];
  return (
    <div style={{
      display: "flex", alignItems: "baseline", justifyContent: "space-between",
      padding: "6px 8px", background: "var(--surface-2)",
      borderRadius: 6,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--ink-2)" }}>
        <span style={{ width: 6, height: 6, borderRadius: 999, background: dot }} />
        {label}
      </div>
      <div className="display tnum" style={{ fontSize: 16, fontWeight: 500 }}>{value}</div>
    </div>
  );
}

// ---------- Debt visualization ----------
function DebtCard({ data }) {
  const max = Math.max(...data.week);
  const state = data.current > 20 ? "elevated" : data.current > 10 ? "moderate" : "healthy";
  const tone = {
    healthy:  { color: "var(--success)", soft: "var(--success-soft)", word: "Healthy" },
    moderate: { color: "var(--ink-2)",   soft: "var(--surface-2)",    word: "Moderate" },
    elevated: { color: "var(--warning)", soft: "var(--warning-soft)", word: "Elevated" },
  }[state];
  return (
    <div>
      <SectionLabel right={<Chip tone="outline">{tone.word}</Chip>}>Email debt</SectionLabel>
      <Card padding={14}>
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", marginBottom: 12 }}>
          <div>
            <div className="display tnum" style={{ fontSize: 36, lineHeight: 1, fontWeight: 400, letterSpacing: "-0.02em" }}>
              {data.current}
            </div>
            <div style={{ fontSize: 11.5, color: "var(--ink-4)", marginTop: 4 }}>
              unanswered, owed by you
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 38 }}>
            {data.week.map((v, i) => (
              <div key={i} style={{
                width: 6, height: `${(v/max)*100}%`,
                background: i === data.week.length-1 ? tone.color : "var(--line-strong)",
                borderRadius: 2,
              }} />
            ))}
          </div>
        </div>
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          paddingTop: 10, borderTop: "1px solid var(--line)",
          fontSize: 12, color: "var(--ink-3)",
        }}>
          <span>+4 since Monday</span>
          <button style={{
            background: "transparent", border: "none", color: "var(--accent)",
            fontSize: 12, fontWeight: 500, cursor: "pointer", padding: 0,
            display: "inline-flex", alignItems: "center", gap: 4,
          }}>Open triage view {Icons.chevron}</button>
        </div>
      </Card>
    </div>
  );
}

// ---------- Commitments ----------
function CommitmentSection({ commitments, compact }) {
  const [items, setItems] = useState(commitments);
  useEffect(() => setItems(commitments), [commitments]);
  const toggle = (id) => setItems(it => it.map(i => i.id === id ? { ...i, status: i.status === "done" ? "pending" : "done" } : i));
  return (
    <div>
      <SectionLabel right={`${items.filter(i=>i.status!=="done").length} open`}>Commitments</SectionLabel>
      <Card padding={0}>
        {items.map((c, i) => (
          <div key={c.id} style={{
            display: "flex", alignItems: "flex-start", gap: 10,
            padding: compact ? "10px 12px" : "12px 12px",
            borderBottom: i < items.length-1 ? "1px solid var(--line)" : "none",
            opacity: c.status === "done" ? 0.5 : 1,
          }}>
            <button onClick={() => toggle(c.id)} style={{
              width: 16, height: 16, borderRadius: 999, marginTop: 1,
              background: c.status === "done" ? "var(--accent)" : "transparent",
              border: `1.5px solid ${c.status === "done" ? "var(--accent)" : c.status === "overdue" ? "var(--warning)" : "var(--line-strong)"}`,
              color: "var(--accent-ink)", padding: 0, display: "grid", placeItems: "center",
              cursor: "pointer", flexShrink: 0,
            }}>
              {c.status === "done" && <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><path d="m5 12 5 5L20 7"/></svg>}
            </button>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{
                fontSize: 13, color: "var(--ink)",
                textDecoration: c.status === "done" ? "line-through" : "none",
              }}>{c.text}</div>
              <div style={{
                display: "flex", alignItems: "center", gap: 6, marginTop: 3,
                fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--font-mono)",
              }}>
                <span>→ {c.to}</span>
                <span>·</span>
                <span style={{ color: c.status === "overdue" ? "var(--warning)" : "var(--ink-4)" }}>
                  {c.status === "overdue" ? "overdue · " : ""}{c.due}
                </span>
              </div>
            </div>
            {!compact && (
              <button style={{
                background: "transparent", border: "none", color: "var(--ink-4)",
                padding: 4, cursor: "pointer", flexShrink: 0,
              }}>{Icons.external}</button>
            )}
          </div>
        ))}
      </Card>
    </div>
  );
}

// ---------- Recent inbox preview ----------
function RecentInboxSection({ inbox, onOpen }) {
  return (
    <div>
      <SectionLabel right="Past hour">Recent</SectionLabel>
      <Card padding={0}>
        {inbox.map((row, i) => (
          <div key={row.id} onClick={() => onOpen && onOpen(row.id)}
               style={{
            display: "flex", alignItems: "flex-start", gap: 10,
            padding: "10px 12px",
            borderBottom: i < inbox.length-1 ? "1px solid var(--line)" : "none",
            cursor: "pointer", transition: "background var(--dur-fast) var(--ease)",
          }}
          onMouseEnter={e => e.currentTarget.style.background = "var(--surface-2)"}
          onMouseLeave={e => e.currentTarget.style.background = "transparent"}
          >
            <Avatar name={row.from} size={24} hue={hueFor(row.from)} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 6 }}>
                <div style={{ fontSize: 12.5, fontWeight: row.unread ? 500 : 400, color: "var(--ink)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.from}</div>
                <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-4)" }}>{row.time}</div>
              </div>
              <div style={{ fontSize: 12, color: "var(--ink-2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginTop: 1 }}>{row.subject}</div>
              <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
                <ReplyChip state={row.reply} />
                {row.commitment && <Chip tone="outline">↩ open commitment</Chip>}
              </div>
            </div>
          </div>
        ))}
      </Card>
    </div>
  );
}

// ---------- Digest CTA ----------
function DigestCTA() {
  return (
    <Card padding={14} style={{ background: "var(--surface-2)", borderStyle: "dashed" }}>
      <div className="mono" style={{ fontSize: 10, color: "var(--ink-4)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 6 }}>Monday digest</div>
      <div style={{ fontSize: 13.5, color: "var(--ink)", marginBottom: 10, lineHeight: 1.45 }}>
        Last week: 3 commitments closed, 2 still open, 14 newsletters auto-archived.
      </div>
      <Button kind="link">Read full digest →</Button>
    </Card>
  );
}

// ---------- Reusable: back-to-inbox sub-header ----------
function BackBar({ onBack, label = "Inbox", right }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 6,
      padding: "8px 14px",
      borderBottom: "1px solid var(--line)",
      background: "var(--bg-elev)",
      flexShrink: 0,
    }}>
      <button onClick={onBack} style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        padding: "4px 10px 4px 6px", borderRadius: 999,
        background: "transparent", border: "1px solid var(--line)",
        color: "var(--ink-2)", fontSize: 12, cursor: "pointer",
        fontFamily: "var(--font-sans)",
        transition: "background var(--dur-fast) var(--ease)",
      }}
      onMouseEnter={e => e.currentTarget.style.background = "var(--surface-2)"}
      onMouseLeave={e => e.currentTarget.style.background = "transparent"}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m15 6-6 6 6 6"/></svg>
        {label}
      </button>
      <div style={{ flex: 1 }} />
      {right}
    </div>
  );
}

// ---------- State: Reading a thread ----------
function ReadingThreadState({ data, onDraft, onBack }) {
  const t = data.openThread;
  return (
    <>
      <BackBar onBack={onBack} right={<Chip tone="outline">3 messages</Chip>} />
      <div className="thin-scroll" style={{ flex: 1, overflowY: "auto", padding: "14px 14px 20px" }}>
      <SectionLabel right={<button style={{
        background: "transparent", border: "none", color: "var(--ink-4)",
        fontSize: 11, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 2,
      }}>open in mail {Icons.external}</button>}>Thread summary</SectionLabel>
      <Card padding={14}>
        <div className="display" style={{
          fontSize: 16, lineHeight: 1.3, fontWeight: 400,
          letterSpacing: "-0.01em", marginBottom: 10, color: "var(--ink)",
        }}>{t.subject}</div>
        <div style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.55, marginBottom: 12 }}>
          {t.summary}
        </div>
        <div style={{ borderTop: "1px solid var(--line)", paddingTop: 10 }}>
          {t.keyPoints.map((kp, i) => (
            <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 8, marginBottom: i < t.keyPoints.length-1 ? 6 : 0 }}>
              <span style={{ color: "var(--accent)", marginTop: 6, width: 4, height: 4, borderRadius: 999, background: "var(--accent)", flexShrink: 0 }} />
              <span style={{ fontSize: 12.5, color: "var(--ink-2)" }}>{kp}</span>
            </div>
          ))}
        </div>
      </Card>

      <div style={{ height: 10 }} />
      <Button kind="primary" full icon={Icons.edit} onClick={onDraft}>Draft a reply in your voice</Button>

      <div style={{ height: 16 }} />
      <SectionLabel>In this thread</SectionLabel>
      <Card padding={0}>
        {t.commitments.map((c, i) => (
          <div key={i} style={{ padding: "12px", display: "flex", alignItems: "flex-start", gap: 10 }}>
            <div style={{ width: 16, height: 16, borderRadius: 999, border: "1.5px solid var(--line-strong)", flexShrink: 0, marginTop: 1 }} />
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13 }}>{c.text}</div>
              <div className="mono" style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 3 }}>
                you committed to this · {c.origin}
              </div>
            </div>
            <Chip tone="warning">due {c.due}</Chip>
          </div>
        ))}
      </Card>

      <div style={{ height: 16 }} />
      <SectionLabel>Related past threads</SectionLabel>
      <Card padding={0}>
        {t.related.map((r, i) => (
          <div key={i} style={{
            padding: "10px 12px",
            borderBottom: i < t.related.length-1 ? "1px solid var(--line)" : "none",
            display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10,
            cursor: "pointer",
          }}>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontSize: 12.5, color: "var(--ink)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.subject}</div>
              <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-4)", marginTop: 2 }}>{r.from} · {r.date}</div>
            </div>
            <span style={{ color: "var(--ink-4)" }}>{Icons.chevron}</span>
          </div>
        ))}
      </Card>
      </div>
    </>
  );
}

// ---------- State: Live draft generation (animated) ----------
function LiveDraftState({ data, onBack, onAccept, context = "thread" }) {
  // Phases: think → stream → done
  const [phase, setPhase] = useState("think");
  const [thinkStep, setThinkStep] = useState(0);
  const [streamed, setStreamed] = useState("");

  // The full draft we'll type out (use the "Brief & direct" variant)
  const fullDraft = data.drafts[0].body;

  // Thinking steps
  const steps = [
    "Reading the full thread",
    "Pulling tone cues from your Sent folder",
    "Checking what you've committed to in this thread",
    "Drafting in your voice",
  ];

  // Advance through thinking steps
  useEffect(() => {
    if (phase !== "think") return;
    if (thinkStep >= steps.length) {
      const t = setTimeout(() => setPhase("stream"), 220);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => setThinkStep(s => s + 1), 380 + (thinkStep === 0 ? 100 : 0));
    return () => clearTimeout(t);
  }, [phase, thinkStep]);

  // Stream the draft (one word at a time, ~24ms cadence)
  useEffect(() => {
    if (phase !== "stream") return;
    if (streamed.length >= fullDraft.length) {
      const t = setTimeout(() => setPhase("done"), 380);
      return () => clearTimeout(t);
    }
    const next = nextChunk(fullDraft, streamed.length);
    const t = setTimeout(() => setStreamed(fullDraft.slice(0, next)), 18 + Math.random() * 26);
    return () => clearTimeout(t);
  }, [phase, streamed, fullDraft]);

  const reset = () => { setPhase("think"); setThinkStep(0); setStreamed(""); };

  return (
    <>
      <BackBar onBack={onBack} label={context === "overdue" ? "Back to commitments" : "Back to thread"}
        right={
          <Chip tone="warm">
            <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
              <span style={{
                width: 5, height: 5, borderRadius: 999, background: "var(--accent)",
                animation: phase !== "done" ? "pulse 1s infinite var(--ease)" : "none",
              }} />
              {phase === "think" ? "thinking" : phase === "stream" ? "writing" : "ready"}
            </span>
          </Chip>
        }
      />

      <div className="thin-scroll" style={{ flex: 1, overflowY: "auto", padding: "12px 14px 20px" }}>
        {/* Recipient context strip */}
        <div style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "8px 12px", marginBottom: 10,
          background: "var(--surface-2)", borderRadius: 8,
        }}>
          <span className="mono" style={{ fontSize: 10, color: "var(--ink-4)", letterSpacing: "0.06em", textTransform: "uppercase" }}>re:</span>
          <span style={{ fontSize: 12.5, color: "var(--ink-2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
            {context === "overdue" ? "Customer escalation: Helix Health → Ana Beltrán" : data.openThread.subject + " → Priya Ramaswamy"}
          </span>
        </div>

        {/* Thinking phase: animated steps */}
        {phase === "think" && (
          <Card padding={14}>
            <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 10 }}>
              MailMind · reasoning
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {steps.map((s, i) => (
                <div key={i} style={{
                  display: "flex", alignItems: "center", gap: 10,
                  fontSize: 13,
                  color: i < thinkStep ? "var(--ink)" : i === thinkStep ? "var(--ink)" : "var(--ink-4)",
                  opacity: i <= thinkStep ? 1 : 0.4,
                  transition: "all var(--dur) var(--ease)",
                }}>
                  <span style={{
                    width: 16, height: 16, borderRadius: 999, flexShrink: 0,
                    border: `1.5px solid ${i < thinkStep ? "var(--accent)" : "var(--line-strong)"}`,
                    background: i < thinkStep ? "var(--accent)" : "transparent",
                    color: "var(--accent-ink)",
                    display: "grid", placeItems: "center",
                  }}>
                    {i < thinkStep && <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><path d="m5 12 5 5L20 7"/></svg>}
                    {i === thinkStep && <span style={{
                      width: 6, height: 6, borderRadius: 999, background: "var(--accent)",
                      animation: "pulse 1s infinite var(--ease)",
                    }} />}
                  </span>
                  <span style={{ flex: 1 }}>{s}</span>
                  {i === thinkStep && <span style={{ color: "var(--ink-4)" }}><Dots /></span>}
                </div>
              ))}
            </div>
          </Card>
        )}

        {/* Stream + done phase: draft canvas */}
        {(phase === "stream" || phase === "done") && (
          <>
            <SectionLabel right={
              phase === "stream"
                ? <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-4)" }}>streaming…</span>
                : <span className="mono" style={{ fontSize: 10.5, color: "var(--success)" }}>1.4s · your voice profile</span>
            }>Draft</SectionLabel>
            <Card padding={14}>
              <div style={{
                fontSize: 14, lineHeight: 1.6, color: "var(--ink)",
                whiteSpace: "pre-wrap",
                minHeight: 120,
              }}>
                {streamed}
                {phase === "stream" && (
                  <span style={{
                    display: "inline-block", width: 7, height: 16,
                    background: "var(--accent)", verticalAlign: "text-bottom",
                    marginLeft: 1, animation: "blink 0.9s infinite steps(2,end)",
                  }} />
                )}
              </div>
            </Card>

            {phase === "done" && (
              <>
                <div style={{ height: 12 }} />
                {/* Citations / where this came from */}
                <Card padding={12} style={{ background: "var(--surface-2)", borderColor: "transparent" }}>
                  <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 8 }}>
                    Drew on
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12.5, color: "var(--ink-2)" }}>
                    <SourcePill icon={Icons.reply} text="Your reply on Tuesday — committed to send notes by Wed AM" />
                    <SourcePill icon={Icons.user}  text="184 messages in your Sent folder for voice tone" />
                    <SourcePill icon={Icons.doc}   text="Attached: Q3-pricing-experiment-results.pdf" />
                  </div>
                </Card>

                <div style={{ height: 12 }} />
                {/* Action chips */}
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6 }}>
                  <Button kind="secondary" size="sm" icon={Icons.edit} onClick={reset} title="Regenerate">Regenerate</Button>
                  <Button kind="secondary" size="sm" icon={Icons.x}>Discard</Button>
                  <Button kind="primary" size="sm" icon={Icons.send} onClick={onAccept}>Send…</Button>
                </div>
                <div style={{ height: 8 }} />
                <Button kind="ghost" size="sm" full icon={Icons.doc}>Save as draft (don't send)</Button>
                <div style={{ height: 12 }} />
                <div className="mono" style={{
                  fontSize: 10.5, color: "var(--ink-4)", textAlign: "center", letterSpacing: "0.04em",
                }}>
                  Edit inline above · 3 variants available
                </div>
              </>
            )}
          </>
        )}
      </div>
    </>
  );
}

// Helper: deal out 1–4 chars per tick so spaces feel like word boundaries.
function nextChunk(text, pos) {
  const remaining = text.length - pos;
  if (remaining <= 0) return text.length;
  const ch = text[pos];
  // If we're at a space or punctuation, advance until next word starts (small jump)
  if (ch === " " || ch === "\n") return Math.min(text.length, pos + 1);
  // Otherwise emit one char (typewriter feel)
  return Math.min(text.length, pos + 1 + Math.floor(Math.random() * 2));
}

function SourcePill({ icon, text }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "4px 0",
    }}>
      <span style={{ color: "var(--accent)", flexShrink: 0 }}>{icon}</span>
      <span style={{ flex: 1 }}>{text}</span>
    </div>
  );
}

// ---------- State: Compose chat (chat → live draft → editable → import) ----------
function ComposeChatState({ data, onBack, onImported }) {
  // sub-phase: chat (gathering context), thinking, streaming, editing, imported
  const [phase, setPhase] = useState("chat");
  const [messages, setMessages] = useState([
    { who: "mm", text: "Who's this to, and what's the gist? You can type or hold the mic to speak." },
  ]);
  const [input, setInput] = useState("");
  const [mic, setMic] = useState("idle"); // idle | listening
  const [interim, setInterim] = useState(""); // streamed transcript
  const [recipient, setRecipient] = useState(null);
  const [subject, setSubject] = useState(null);
  const [streamed, setStreamed] = useState("");
  const [thinkStep, setThinkStep] = useState(0);
  const [editable, setEditable] = useState("");
  const scrollerRef = useRef(null);

  const suggestionsByTurn = [
    ["Reply to Priya about pricing", "Follow-up on Acme contract", "Intro Daniel ↔ Sarah"],
    ["Keep it brief and direct", "Warmer, more detailed", "Push the meeting to Friday"],
  ];

  const suggestionForTurn = () => suggestionsByTurn[Math.min(messages.filter(m => m.who === "you").length, suggestionsByTurn.length - 1)];

  // autoscroll chat
  useEffect(() => {
    if (scrollerRef.current) scrollerRef.current.scrollTop = scrollerRef.current.scrollHeight;
  }, [messages, phase, interim]);

  // Mic simulation: type out a fake transcript when user holds the mic
  useEffect(() => {
    if (mic !== "listening") return;
    const phrases = [
      "Reply to Priya about the pricing experiment — agree we should hold the broad rollout and retest with a narrower discount band. Offer to meet at four today.",
      "Tell Ana I'm on the Helix Health escalation, ETA on the fix is end of day Wednesday. Apologize for the delay.",
    ];
    const pick = phrases[Math.floor(Math.random() * phrases.length)];
    let i = 0;
    const id = setInterval(() => {
      i += 2 + Math.floor(Math.random() * 3);
      setInterim(pick.slice(0, i));
      if (i >= pick.length) clearInterval(id);
    }, 60);
    return () => clearInterval(id);
  }, [mic]);

  const stopMic = () => {
    setMic("idle");
    if (interim.trim()) {
      setInput(prev => (prev ? prev + " " : "") + interim.trim());
      setInterim("");
    }
  };

  const send = (text) => {
    const t = (text ?? input).trim();
    if (!t) return;
    setInput("");
    setMessages(m => [...m, { who: "you", text: t }]);

    // Two-turn conversation: first turn captures context, second turn finalizes
    const youTurns = messages.filter(m => m.who === "you").length + 1;
    if (youTurns === 1) {
      // Infer recipient/subject (lightweight)
      const r = /priya/i.test(t) ? "Priya Ramaswamy"
              : /ana|helix/i.test(t) ? "Ana Beltrán"
              : /daniel|acme/i.test(t) ? "Daniel Okafor"
              : "Priya Ramaswamy";
      const s = /priya|pricing/i.test(t) ? "Re: Q3 OKR review — pricing experiment results"
              : /ana|helix/i.test(t) ? "Re: Customer escalation: Helix Health"
              : /daniel|acme/i.test(t) ? "Re: Contract redlines — Acme renewal"
              : "Re: Q3 OKR review — pricing experiment results";
      setRecipient(r); setSubject(s);
      setTimeout(() => setMessages(m => [...m, {
        who: "mm", text: `Got it — replying to ${r}. Anything about tone or anything specific I should reference?`
      }]), 600);
    } else {
      // Kick off generation
      setTimeout(() => generate(), 350);
    }
  };

  const generate = () => {
    setPhase("thinking");
    setThinkStep(0);
  };

  // Animate the four thinking steps
  useEffect(() => {
    if (phase !== "thinking") return;
    const steps = 4;
    if (thinkStep >= steps) {
      const t = setTimeout(() => setPhase("streaming"), 220);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => setThinkStep(s => s + 1), 360);
    return () => clearTimeout(t);
  }, [phase, thinkStep]);

  const fullDraft = data.drafts[0].body;

  // Stream the draft
  useEffect(() => {
    if (phase !== "streaming") return;
    if (streamed.length >= fullDraft.length) {
      setEditable(fullDraft);
      const t = setTimeout(() => setPhase("editing"), 220);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => setStreamed(fullDraft.slice(0, streamed.length + 1)), 16 + Math.random() * 20);
    return () => clearTimeout(t);
  }, [phase, streamed, fullDraft]);

  const reset = () => {
    setPhase("chat");
    setMessages([{ who: "mm", text: "Try again — what's the new angle?" }]);
    setStreamed(""); setEditable(""); setThinkStep(0);
  };

  return (
    <>
      <BackBar onBack={onBack} label="Close compose"
        right={phase === "chat" ? <Chip tone="warm">new draft</Chip> :
               phase === "thinking" ? <Chip tone="warm"><span style={{display:"inline-flex",alignItems:"center",gap:4}}><span style={{width:5,height:5,borderRadius:999,background:"var(--accent)",animation:"pulse 1s infinite var(--ease)"}}/>thinking</span></Chip> :
               phase === "streaming" ? <Chip tone="warm"><span style={{display:"inline-flex",alignItems:"center",gap:4}}><span style={{width:5,height:5,borderRadius:999,background:"var(--accent)",animation:"pulse 1s infinite var(--ease)"}}/>writing</span></Chip> :
               phase === "editing" ? <Chip tone="success">ready · editable</Chip> :
               <Chip tone="success">imported to drafts</Chip>}
      />

      {/* RECIPIENT BAR (visible after first turn) */}
      {recipient && (
        <div style={{
          padding: "8px 14px",
          background: "var(--surface-2)",
          borderBottom: "1px solid var(--line)",
          display: "flex", alignItems: "center", gap: 10,
          flexShrink: 0,
        }}>
          <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.08em", textTransform: "uppercase" }}>to</span>
          <Avatar name={recipient} size={20} hue={hueFor(recipient)} />
          <span style={{ fontSize: 12.5, color: "var(--ink), fontWeight: 500", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{recipient}</span>
          <button style={{
            background: "transparent", border: "none", color: "var(--ink-4)",
            fontSize: 11, cursor: "pointer", fontFamily: "var(--font-mono)",
          }}>change</button>
        </div>
      )}

      {/* CHAT PHASE */}
      {phase === "chat" && (
        <>
          <div ref={scrollerRef} className="thin-scroll" style={{ flex: 1, overflowY: "auto", padding: "14px 14px 8px", display: "flex", flexDirection: "column", gap: 10 }}>
            {messages.map((m, i) => <Bubble key={i} who={m.who} text={m.text} />)}
            {mic === "listening" && interim && (
              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <div style={{
                  maxWidth: "86%",
                  background: "var(--accent-soft)", color: "var(--ink-2)",
                  border: "1px dashed var(--accent-line)",
                  borderRadius: "12px 12px 4px 12px",
                  padding: "8px 12px", fontSize: 13, lineHeight: 1.5,
                  fontStyle: "italic",
                }}>
                  <div className="mono" style={{ fontSize: 9.5, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--accent)", marginBottom: 3 }}>
                    listening…
                  </div>
                  {interim}<span className="blink-caret" style={{ display: "inline-block", width: 6, height: 13, background: "var(--accent)", verticalAlign: "text-bottom", marginLeft: 2, animation: "blink 0.9s steps(2) infinite" }} />
                </div>
              </div>
            )}
          </div>

          {/* Suggestion chips */}
          <div style={{ padding: "0 14px 8px", display: "flex", gap: 6, flexWrap: "wrap", flexShrink: 0 }}>
            {suggestionForTurn().map((s, i) => (
              <button key={i} onClick={() => send(s)} style={{
                padding: "4px 10px", borderRadius: 999,
                background: "var(--surface-2)", border: "1px solid var(--line)",
                color: "var(--ink-2)", fontSize: 11.5, cursor: "pointer",
                fontFamily: "var(--font-sans)", whiteSpace: "nowrap",
              }}>{s}</button>
            ))}
          </div>

          {/* Input row */}
          <div style={{
            padding: 12, borderTop: "1px solid var(--line)",
            display: "flex", alignItems: "flex-end", gap: 8,
            background: "var(--bg-elev)", flexShrink: 0,
          }}>
            <div style={{
              flex: 1, background: "var(--surface)",
              border: `1px solid ${input ? "var(--accent)" : "var(--line)"}`,
              borderRadius: 10, display: "flex", alignItems: "flex-end", gap: 6,
              padding: "8px 10px",
              transition: "border-color var(--dur-fast) var(--ease)",
            }}>
              <textarea
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                rows={1}
                placeholder={messages.filter(m=>m.who==="you").length === 0 ? "Tell MailMind what to draft…" : "Anything specific to reference?"}
                style={{
                  flex: 1, border: "none", outline: "none", background: "transparent",
                  fontSize: 13, color: "var(--ink)", fontFamily: "var(--font-sans)",
                  resize: "none", lineHeight: 1.5, maxHeight: 100,
                  padding: "2px 0",
                }}
              />
            </div>
            <button
              onMouseDown={() => setMic("listening")}
              onMouseUp={stopMic}
              onMouseLeave={() => { if (mic === "listening") stopMic(); }}
              style={{
                width: 36, height: 36, borderRadius: 999,
                background: mic === "listening" ? "var(--danger)" : "var(--surface-2)",
                color: mic === "listening" ? "white" : "var(--ink-2)",
                border: "1px solid var(--line)",
                display: "grid", placeItems: "center", cursor: "pointer",
                position: "relative",
              }} title="Hold to speak">
              {mic === "listening" && <span style={{
                position: "absolute", inset: -3, borderRadius: 999,
                border: "2px solid var(--danger)", opacity: 0.5,
                animation: "pulse 1.1s infinite var(--ease)",
              }} />}
              {Icons.mic}
            </button>
            <button onClick={() => send()} disabled={!input.trim() && !interim.trim()} style={{
              width: 36, height: 36, borderRadius: 999, border: "none",
              background: (input.trim() || interim.trim()) ? "var(--accent)" : "var(--surface-2)",
              color: (input.trim() || interim.trim()) ? "var(--accent-ink)" : "var(--ink-4)",
              cursor: (input.trim() || interim.trim()) ? "pointer" : "not-allowed",
              display: "grid", placeItems: "center",
              transition: "all var(--dur-fast) var(--ease)",
            }}>{Icons.arrow}</button>
          </div>
          <div className="mono" style={{
            fontSize: 9.5, color: "var(--ink-4)", textAlign: "center",
            padding: "0 0 10px", letterSpacing: "0.04em",
          }}>
            hold mic to dictate · ↵ to send · ⇧↵ for newline
          </div>
        </>
      )}

      {/* THINKING PHASE */}
      {phase === "thinking" && (
        <div className="thin-scroll" style={{ flex: 1, overflowY: "auto", padding: "14px 14px 20px" }}>
          <Card padding={14}>
            <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 12 }}>
              MailMind · reasoning
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {["Reading the open thread", "Pulling tone cues from your Sent folder", "Checking what you've committed to", "Drafting in your voice"].map((s, i) => (
                <div key={i} style={{
                  display: "flex", alignItems: "center", gap: 10,
                  fontSize: 13,
                  opacity: i <= thinkStep ? 1 : 0.4,
                  color: i < thinkStep ? "var(--ink)" : i === thinkStep ? "var(--ink)" : "var(--ink-4)",
                  transition: "all var(--dur) var(--ease)",
                }}>
                  <span style={{
                    width: 16, height: 16, borderRadius: 999, flexShrink: 0,
                    border: `1.5px solid ${i < thinkStep ? "var(--accent)" : "var(--line-strong)"}`,
                    background: i < thinkStep ? "var(--accent)" : "transparent",
                    color: "var(--accent-ink)",
                    display: "grid", placeItems: "center",
                  }}>
                    {i < thinkStep && <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><path d="m5 12 5 5L20 7"/></svg>}
                    {i === thinkStep && <span style={{ width: 6, height: 6, borderRadius: 999, background: "var(--accent)", animation: "pulse 1s infinite var(--ease)" }} />}
                  </span>
                  <span style={{ flex: 1 }}>{s}</span>
                  {i === thinkStep && <span style={{ color: "var(--ink-4)" }}><Dots /></span>}
                </div>
              ))}
            </div>
          </Card>
        </div>
      )}

      {/* STREAMING + EDITING PHASE */}
      {(phase === "streaming" || phase === "editing" || phase === "imported") && (
        <div className="thin-scroll" style={{ flex: 1, overflowY: "auto", padding: "12px 14px 14px", display: "flex", flexDirection: "column" }}>
          {/* Subject preview */}
          {subject && (
            <div style={{
              padding: "8px 12px", marginBottom: 10,
              background: "var(--surface-2)", borderRadius: 6,
              display: "flex", alignItems: "center", gap: 8,
            }}>
              <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.08em", textTransform: "uppercase" }}>subj</span>
              <span style={{ fontSize: 12.5, color: "var(--ink-2)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{subject}</span>
            </div>
          )}

          <SectionLabel right={
            phase === "streaming" ? <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-4)" }}>streaming…</span> :
            phase === "imported" ? <span className="mono" style={{ fontSize: 10.5, color: "var(--success)" }}>saved to your drafts</span> :
            <span className="mono" style={{ fontSize: 10.5, color: "var(--success)" }}>1.4s · your voice</span>
          }>{phase === "imported" ? "Imported" : "Draft"}</SectionLabel>

          {phase === "streaming" ? (
            <Card padding={14}>
              <div style={{
                fontSize: 14, lineHeight: 1.6, color: "var(--ink)",
                whiteSpace: "pre-wrap", minHeight: 140,
              }}>
                {streamed}
                <span style={{
                  display: "inline-block", width: 7, height: 16,
                  background: "var(--accent)", verticalAlign: "text-bottom",
                  marginLeft: 1, animation: "blink 0.9s infinite steps(2,end)",
                }} />
              </div>
            </Card>
          ) : (
            <div style={{
              background: "var(--surface)", border: `1px solid ${phase === "imported" ? "var(--success)" : "var(--accent)"}`,
              borderRadius: 8, padding: 0, position: "relative",
              boxShadow: phase === "imported" ? "0 0 0 3px var(--success-soft)" : "0 0 0 3px color-mix(in oklab, var(--accent) 14%, transparent)",
              transition: "all var(--dur) var(--ease)",
            }}>
              <textarea
                value={editable}
                onChange={e => setEditable(e.target.value)}
                readOnly={phase === "imported"}
                spellCheck={false}
                style={{
                  width: "100%", minHeight: 220,
                  border: "none", outline: "none", background: "transparent",
                  padding: 14, resize: "vertical",
                  fontSize: 14, lineHeight: 1.6, color: "var(--ink)",
                  fontFamily: "var(--font-sans)",
                }}
              />
              <div style={{
                position: "absolute", top: 8, right: 8,
                padding: "3px 7px", borderRadius: 4,
                background: phase === "imported" ? "var(--success-soft)" : "var(--accent-soft)",
                color: phase === "imported" ? "var(--success)" : "var(--accent)",
                fontFamily: "var(--font-mono)", fontSize: 9.5, letterSpacing: "0.04em",
                border: `1px solid ${phase === "imported" ? "color-mix(in oklab, var(--success) 30%, transparent)" : "var(--accent-line)"}`,
              }}>
                {phase === "imported" ? "read-only" : "editable"}
              </div>
            </div>
          )}

          {/* Provenance */}
          {phase !== "streaming" && (
            <>
              <div style={{ height: 12 }} />
              <Card padding={10} style={{ background: "var(--surface-2)", borderColor: "transparent" }}>
                <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 6 }}>Drew on</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12, color: "var(--ink-2)" }}>
                  <SourcePill icon={Icons.user} text="184 sent messages for tone" />
                  <SourcePill icon={Icons.reply} text="Your 5:48pm reply on Tuesday — committed to send notes" />
                </div>
              </Card>
            </>
          )}

          <div style={{ flex: 1 }} />

          {/* Action footer */}
          {phase === "editing" && (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 12 }}>
                <Button kind="secondary" size="md" icon={Icons.edit} onClick={reset}>Regenerate</Button>
                <Button kind="primary" size="md" icon={Icons.send} onClick={() => {
                  // Approve as-is → simulate Gmail import
                  setPhase("imported");
                  setTimeout(() => onImported && onImported(), 1600);
                }}>Approve & import</Button>
              </div>
              <div style={{ height: 8 }} />
              <Button kind="ghost" size="sm" full icon={Icons.x} onClick={onBack}>Discard draft</Button>
              <div className="mono" style={{
                fontSize: 10, color: "var(--ink-4)", textAlign: "center",
                marginTop: 10, letterSpacing: "0.04em", lineHeight: 1.5,
              }}>
                Edit text above before importing · imports as a Gmail draft<br/>
                (not sent — you press Send in Gmail when ready)
              </div>
            </>
          )}

          {phase === "imported" && (
            <Card padding={14} style={{ marginTop: 12, background: "var(--success-soft)", borderColor: "color-mix(in oklab, var(--success) 30%, transparent)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
                <span style={{ color: "var(--success)" }}>{Icons.checkCircle}</span>
                <div className="display" style={{ fontSize: 14, fontWeight: 500 }}>Imported to Gmail drafts</div>
              </div>
              <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginBottom: 10 }}>
                Your reply is waiting in {recipient}'s thread. Nothing was sent — you press Send in Gmail when ready.
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <Button kind="primary" size="sm" icon={Icons.external}>Open in Gmail</Button>
                <Button kind="ghost" size="sm" icon={Icons.undo} onClick={reset}>Undo & redraft</Button>
              </div>
            </Card>
          )}
        </div>
      )}
    </>
  );
}

// ---------- State: Drafting (multi-variant chooser) ----------
function DraftingState({ data, onSend, onBack }) {
  const [selected, setSelected] = useState(0);
  const variants = data.drafts;
  const chosen = variants[selected];
  return (
    <div className="thin-scroll" style={{ flex: 1, overflowY: "auto", padding: "14px 14px 20px" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
        <button onClick={onBack} style={{
          display: "inline-flex", alignItems: "center", gap: 4,
          background: "transparent", border: "none", color: "var(--ink-3)",
          fontSize: 12, cursor: "pointer", padding: 0,
        }}>{Icons.back} <span>Back to thread</span></button>
        <Chip tone="outline">re: Q3 OKR review</Chip>
      </div>

      <SectionLabel right="3 variants">Draft a reply</SectionLabel>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {variants.map((v, i) => {
          const active = i === selected;
          return (
            <div key={i} onClick={() => setSelected(i)} style={{
              border: `1px solid ${active ? "var(--accent)" : "var(--line)"}`,
              background: active ? "color-mix(in oklab, var(--accent-soft) 50%, var(--surface))" : "var(--surface)",
              borderRadius: "var(--r-md)", padding: 12, cursor: "pointer",
              transition: "all var(--dur-fast) var(--ease)",
              boxShadow: active ? "0 0 0 3px color-mix(in oklab, var(--accent) 12%, transparent)" : "none",
            }}>
              <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 6 }}>
                <div className="display" style={{ fontSize: 13.5, fontWeight: 500, color: "var(--ink)" }}>{v.tone}</div>
                <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-4)" }}>{v.length}</div>
              </div>
              {active ? (
                <div style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.55, whiteSpace: "pre-wrap" }}>
                  {v.body}
                </div>
              ) : (
                <div style={{ fontSize: 12.5, color: "var(--ink-3)", lineHeight: 1.5, overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
                  {v.body.split("\n")[0]}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div style={{ height: 14 }} />
      <Card padding={12} style={{ background: "var(--warning-soft)", borderColor: "color-mix(in oklab, var(--warning) 25%, transparent)" }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
          <span style={{ color: "var(--warning)", marginTop: 1 }}>{Icons.info}</span>
          <div>
            <div style={{ fontSize: 12.5, color: "var(--ink)", fontWeight: 500 }}>You promised to send notes by tomorrow AM.</div>
            <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 2 }}>From your reply on Tuesday — worth referencing in this draft.</div>
          </div>
        </div>
      </Card>

      <div style={{ height: 14 }} />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <Button kind="secondary" icon={Icons.edit}>Edit</Button>
        <Button kind="primary" icon={Icons.send} onClick={onSend}>Send…</Button>
      </div>
    </div>
  );
}

// ---------- State: Voice session ----------
function VoiceState({ onEnd, onBack }) {
  const [phase, setPhase] = useState("listening"); // listening, processing, speaking
  const bars = useMemo(() => Array.from({length: 28}, (_,i) => i), []);
  const [bump, setBump] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setBump(b => b+1), 110);
    return () => clearInterval(id);
  }, []);

  const seq = ["listening", "processing", "speaking"];
  const advance = () => setPhase(p => seq[(seq.indexOf(p)+1) % seq.length]);

  return (
    <>
      <BackBar onBack={onBack} label="Back to thread" right={<Chip tone="warm">grounded in current thread</Chip>} />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", padding: "14px 14px 20px" }}>
      <SectionLabel>Voice session</SectionLabel>

      <div className="thin-scroll" style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 10, paddingRight: 2 }}>
        <Bubble who="you" text="What's Priya actually asking for?" />
        <Bubble who="mm"  text="She wants your read on whether to roll out the pricing experiment broadly. Conversion is up but ARPU is flat, and she's leaning toward a narrower retest. She needs your answer before Friday." />
        <Bubble who="you" text="Did I commit to anything in this thread already?" />
        <Bubble who="mm"  text="Yes — you said you'd send notes by Wednesday morning. That's tomorrow." />
        {phase === "speaking" && <Bubble who="mm" text="Want me to draft a brief reply pulling from your Tuesday note?" pulse />}
      </div>

      <div style={{ height: 10 }} />
      <Card padding={14} style={{ background: "var(--surface-2)", borderColor: "transparent" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{
              width: 7, height: 7, borderRadius: 999,
              background: phase === "listening" ? "var(--accent)" : phase === "processing" ? "var(--warning)" : "var(--success)",
              animation: "pulse 1.4s infinite var(--ease)",
            }} />
            <span style={{ fontSize: 12, color: "var(--ink-2)" }}>
              {phase === "listening" ? "Listening…" : phase === "processing" ? "Thinking…" : "Replying…"}
            </span>
          </div>
          <button onClick={advance} className="mono" style={{
            background: "transparent", border: "none", color: "var(--ink-4)",
            fontSize: 10.5, cursor: "pointer", letterSpacing: "0.04em",
          }}>cycle state</button>
        </div>
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "center", gap: 3,
          height: 44,
        }}>
          {bars.map(i => {
            const t = (bump + i*3) * 0.4;
            const h = phase === "listening"
              ? Math.max(3, Math.abs(Math.sin(t) * 22) + Math.sin(t*2.3)*8 + 6)
              : phase === "processing" ? 3 + Math.abs(Math.sin(i*0.5 + bump*0.1)) * 4
              : Math.max(3, Math.abs(Math.sin(t*1.6) * 14) + 4);
            return <span key={i} style={{
              width: 3, height: h,
              background: phase === "listening" ? "var(--accent)" : phase === "speaking" ? "var(--ink)" : "var(--ink-4)",
              borderRadius: 2, transition: "height 100ms linear",
            }} />;
          })}
        </div>
        <div style={{ height: 10 }} />
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--font-mono)" }}>
          <span>hold mic to talk · release to send</span>
          <button onClick={onEnd} style={{ background: "transparent", border: "none", color: "var(--danger)", cursor: "pointer", fontSize: 11, fontFamily: "var(--font-mono)" }}>end session</button>
        </div>
      </Card>
      </div>
    </>
  );
}

function Bubble({ who, text, pulse }) {
  const isYou = who === "you";
  return (
    <div style={{ display: "flex", justifyContent: isYou ? "flex-end" : "flex-start" }}>
      <div style={{
        maxWidth: "86%",
        background: isYou ? "var(--ink)" : "var(--surface)",
        color: isYou ? "var(--bg)" : "var(--ink)",
        border: isYou ? "none" : "1px solid var(--line)",
        borderRadius: isYou ? "12px 12px 4px 12px" : "12px 12px 12px 4px",
        padding: "8px 12px", fontSize: 13, lineHeight: 1.5,
        animation: "fadeIn var(--dur) var(--ease)",
        ...(pulse ? { boxShadow: "0 0 0 3px color-mix(in oklab, var(--accent) 18%, transparent)" } : {}),
      }}>
        {!isYou && <div className="mono" style={{
          fontSize: 9.5, letterSpacing: "0.08em", textTransform: "uppercase",
          color: "var(--ink-4)", marginBottom: 3,
        }}>MailMind</div>}
        {text}
      </div>
    </div>
  );
}

// ---------- State: Viewing an email with attachments ----------
function AttachmentState({ data, onAsk, onBack }) {
  const a = data.attachments[0];
  return (
    <>
      <BackBar onBack={onBack} right={<Chip tone="outline">1 attachment</Chip>} />
      <div className="thin-scroll" style={{ flex: 1, overflowY: "auto", padding: "14px 14px 20px" }}>
      <SectionLabel>Attached</SectionLabel>
      <Card padding={0}>
        <div style={{ padding: 12, display: "flex", alignItems: "flex-start", gap: 10, borderBottom: "1px solid var(--line)" }}>
          <div style={{
            width: 32, height: 40, borderRadius: 4,
            background: "var(--accent-soft)", color: "var(--accent)",
            display: "grid", placeItems: "center", flexShrink: 0,
            fontFamily: "var(--font-mono)", fontSize: 9, fontWeight: 500,
          }}>PDF</div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, color: "var(--ink)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.name}</div>
            <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-4)", marginTop: 2 }}>{a.kind.toUpperCase()} · {a.size} · 12 pages</div>
          </div>
        </div>
        <div style={{ padding: 12, borderBottom: "1px solid var(--line)" }}>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 6 }}>Summary</div>
          <div style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.55 }}>{a.summary}</div>
        </div>
        <div style={{ padding: 12 }}>
          <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-4)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 8 }}>Key data</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {a.extracts.map((e, i) => (
              <div key={i} style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 12.5 }}>
                <span style={{ color: "var(--ink-3)" }}>{e.k}</span>
                <span style={{ color: "var(--ink)", textAlign: "right", fontFamily: e.v.match(/[0-9%]/) ? "var(--font-mono)" : "inherit", fontSize: e.v.match(/[0-9%]/) ? 12 : 12.5 }}>{e.v}</span>
              </div>
            ))}
          </div>
        </div>
      </Card>

      <div style={{ height: 10 }} />
      <button onClick={onAsk} style={{
        width: "100%", textAlign: "left",
        background: "var(--surface)", border: "1px solid var(--line)",
        borderRadius: "var(--r-md)", padding: 12, cursor: "pointer",
        display: "flex", alignItems: "center", gap: 10,
        transition: "border-color var(--dur-fast) var(--ease)",
      }}
      onMouseEnter={e => e.currentTarget.style.borderColor = "var(--accent)"}
      onMouseLeave={e => e.currentTarget.style.borderColor = "var(--line)"}
      >
        <span style={{ color: "var(--accent)" }}>{Icons.sparkle}</span>
        <span style={{ flex: 1, fontSize: 13, color: "var(--ink-2)" }}>Ask about this document…</span>
        <span style={{ color: "var(--ink-4)" }}>{Icons.chevron}</span>
      </button>

      <div style={{ height: 14 }} />
      <SectionLabel>Suggested questions</SectionLabel>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {[
          "What's the recommended next step?",
          "How did Tier-1 cohort respond?",
          "What's the p-value on ARPU?",
        ].map((q, i) => (
          <button key={i} style={{
            textAlign: "left", padding: "8px 12px", borderRadius: 6,
            background: "var(--surface-2)", border: "1px solid transparent",
            fontSize: 12.5, color: "var(--ink-2)", cursor: "pointer",
            transition: "border-color var(--dur-fast) var(--ease)",
          }}
          onMouseEnter={e => e.currentTarget.style.borderColor = "var(--line)"}
          onMouseLeave={e => e.currentTarget.style.borderColor = "transparent"}
          >{q}</button>
        ))}
      </div>
      </div>
    </>
  );
}

// ---------- Action log overlay ----------
function ActionLogOverlay({ actions, onClose, onUndo }) {
  return (
    <div style={{
      position: "absolute", inset: 0,
      background: "color-mix(in oklab, var(--bg) 80%, transparent)",
      backdropFilter: "blur(6px)",
      zIndex: 20, animation: "fadeIn var(--dur) var(--ease)",
      display: "flex", flexDirection: "column",
    }}>
      <div style={{
        padding: "14px 14px 10px", display: "flex",
        alignItems: "center", justifyContent: "space-between",
        borderBottom: "1px solid var(--line)",
      }}>
        <div className="display" style={{ fontSize: 16, fontWeight: 500 }}>Action log</div>
        <button onClick={onClose} style={{ background: "transparent", border: "none", color: "var(--ink-3)", cursor: "pointer", padding: 4 }}>{Icons.x}</button>
      </div>
      <div className="thin-scroll" style={{ flex: 1, overflowY: "auto", padding: "10px 14px 14px" }}>
        {actions.map((a, i) => (
          <div key={a.id} style={{
            display: "flex", alignItems: "flex-start", gap: 10,
            padding: "10px 0", borderBottom: i < actions.length-1 ? "1px solid var(--line)" : "none",
          }}>
            <span style={{ width: 6, height: 6, borderRadius: 999, background: "var(--accent)", marginTop: 7, flexShrink: 0 }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13, color: "var(--ink)" }}>
                <span style={{ fontWeight: 500 }}>{a.verb}</span>: {a.target}
              </div>
              <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-4)", marginTop: 2 }}>{a.time}</div>
            </div>
            {a.reversible && (
              <button onClick={() => onUndo && onUndo(a.id)} style={{
                background: "transparent", border: "1px solid var(--line)",
                color: "var(--ink-2)", padding: "3px 8px", borderRadius: 4,
                fontSize: 11, cursor: "pointer", fontFamily: "var(--font-mono)",
              }}>{a.kind === "open" ? "open" : "undo"}</button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------- Confirmation modal (Send) ----------
function ConfirmSendModal({ onCancel, onConfirm, draft }) {
  return (
    <div style={{
      position: "absolute", inset: 0, zIndex: 40,
      background: "rgba(0,0,0,0.4)",
      display: "grid", placeItems: "center",
      padding: 16, animation: "fadeIn var(--dur) var(--ease)",
    }}>
      <div style={{
        background: "var(--bg-elev)", borderRadius: "var(--r-lg)",
        border: "1px solid var(--line)", padding: 18, width: "100%",
        boxShadow: "var(--shadow-lg)", animation: "slideUp var(--dur) var(--ease)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
          <span style={{ color: "var(--warning)" }}>{Icons.warning}</span>
          <div className="display" style={{ fontSize: 16, fontWeight: 500 }}>Send this reply?</div>
        </div>
        <div style={{ fontSize: 12.5, color: "var(--ink-3)", marginBottom: 12 }}>
          Sending is permanent and can't be undone from the action log.
        </div>
        <div style={{
          background: "var(--surface)", border: "1px solid var(--line)",
          borderRadius: 6, padding: 10, marginBottom: 12,
        }}>
          <div className="mono" style={{ fontSize: 10, color: "var(--ink-4)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 6 }}>To</div>
          <div style={{ fontSize: 12.5, color: "var(--ink)", marginBottom: 8 }}>
            Priya Ramaswamy &lt;priya@northgrid.co&gt;
          </div>
          <div className="mono" style={{ fontSize: 10, color: "var(--ink-4)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 6 }}>Subject</div>
          <div style={{ fontSize: 12.5, color: "var(--ink)" }}>
            Re: Q3 OKR review — pricing experiment results
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <Button kind="secondary" onClick={onCancel}>Cancel</Button>
          <Button kind="primary" icon={Icons.send} onClick={onConfirm}>Send now</Button>
        </div>
      </div>
    </div>
  );
}

// ---------- Side panel container ----------
function SidePanel({ state, setState, data, width, theme }) {
  const [query, setQuery] = useState("");
  const [micState, setMicState] = useState("idle");
  const [showLog, setShowLog] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [toast, setToast] = useState(null);
  const tier = state === "signedOut" ? 0 : 2;

  const onMic = (intent) => {
    if (intent === "toggle") {
      if (state === "voice") setState("reading");
      else setState("voice");
    }
  };

  const handleSendConfirm = () => {
    setShowConfirm(false);
    setToast({ message: "Sent to Priya Ramaswamy", action: undefined });
    setState("reading");
    setTimeout(() => setToast(null), 3500);
  };

  return (
    <div style={{
      width, height: "100%",
      background: "var(--bg)",
      borderLeft: "1px solid var(--line)",
      display: "flex", flexDirection: "column",
      position: "relative",
      overflow: "hidden",
      transition: "width var(--dur) var(--ease)",
    }}>
      {state !== "signedOut" && (
        <SidePanelHeader tier={tier} query={query} setQuery={setQuery} />
      )}

      {state === "signedOut"   && <SignedOutState onSignIn={() => setState("noTab")} />}
      {state === "noTab"       && <NoGmailTabState data={data} onOpenThread={() => setState("reading")} />}
      {state === "inbox"       && <InboxContextState data={data} />}
      {state === "reading"     && <ReadingThreadState data={data} onDraft={() => setState("liveDraft")} onBack={() => setState("inbox")} />}
      {state === "drafting"    && <DraftingState data={data} onSend={() => setShowConfirm(true)} onBack={() => setState("reading")} />}
      {state === "liveDraft"   && <LiveDraftState data={data} onBack={() => setState("reading")} onAccept={() => setShowConfirm(true)} />}
      {state === "composeChat" && <ComposeChatState data={data} onBack={() => setState("inbox")} onImported={() => {}} />}
      {state === "voice"       && <VoiceState onEnd={() => setState("reading")} onBack={() => setState("reading")} />}
      {state === "attachment"  && <AttachmentState data={data} onBack={() => setState("reading")} />}
      {state === "tier1"       && <Tier1State data={data} />}
      {state === "overdue"     && <OverdueState data={data} onDraft={() => setState("liveDraft")} />}
      {state === "search"      && <SearchState data={data} />}
      {state === "digestEmpty" && <EmptyDigestState />}
      {state === "error"       && <ErrorState onRetry={() => setState("inbox")} />}
      {state === "actionTaken" && <ActionTakenState data={data} />}

      {state !== "signedOut" && (
        <SidePanelFooter
          onMic={onMic} micState={micState}
          onLog={() => setShowLog(true)} logCount={data.actions.length}
        />
      )}

      <Toast visible={!!toast} message={toast?.message} onDismiss={() => setToast(null)} />
      {showLog && <ActionLogOverlay actions={data.actions} onClose={() => setShowLog(false)} onUndo={() => {}} />}
      {showConfirm && <ConfirmSendModal onCancel={() => setShowConfirm(false)} onConfirm={handleSendConfirm} />}
    </div>
  );
}

Object.assign(window, { SidePanel, DebtCard, CommitmentSection, ReadingThreadState, DraftingState, VoiceState, AttachmentState, ActionLogOverlay, ConfirmSendModal, SidePanelHeader, SidePanelFooter });
