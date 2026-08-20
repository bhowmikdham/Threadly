// Phase 4 dashboard screens: Training status, Account & settings, Audit log.

const { useState: useS_de, useEffect: useE_de, useMemo: useM_de } = React;

// ---------- Training status ----------
function TrainingStatus() {
  return (
    <div style={{ maxWidth: 880, margin: "0 auto", padding: "40px 32px 64px" }}>
      <Breadcrumb items={["Account", "Training"]} />
      <div className="display" style={{ fontSize: 32, fontWeight: 500, letterSpacing: "-0.025em", margin: "8px 0 6px" }}>
        Training your voice
      </div>
      <p style={{ color: "var(--ink-3)", fontSize: 15, margin: "0 0 36px", maxWidth: 560 }}>
        MailMind learns your writing voice in stages. Each stage unlocks a sharper feel — and you'll see your drafts get closer to how you'd actually write.
      </p>

      <div style={{
        background: "var(--surface)", border: "1px solid var(--line)",
        borderRadius: 12, padding: 24, marginBottom: 24,
        display: "flex", alignItems: "center", gap: 24,
      }}>
        <BigTierBadge tier={2} />
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
            <span className="display" style={{ fontSize: 21, fontWeight: 500 }}>Tier 2 — Training in progress</span>
            <Chip tone="warm">~ 6 hours remaining</Chip>
          </div>
          <div style={{ fontSize: 13.5, color: "var(--ink-3)", marginBottom: 14, lineHeight: 1.5 }}>
            We're fine-tuning a small model on 184 of your sent messages. Drafts already use voice retrieval; when training finishes, drafts will feel more native.
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ flex: 1, height: 6, borderRadius: 3, background: "var(--surface-2)", overflow: "hidden" }}>
              <div style={{ width: "68%", height: "100%", background: "var(--accent)" }} />
            </div>
            <span className="mono tnum" style={{ fontSize: 11, color: "var(--ink-3)" }}>68%</span>
          </div>
        </div>
      </div>

      <SectionLabel>Tiers</SectionLabel>
      <div style={{
        background: "var(--surface)", border: "1px solid var(--line)",
        borderRadius: 12, overflow: "hidden",
      }}>
        {[
          { n: 0, title: "Voice calibration", state: "done", note: "Completed in onboarding · 38s sample" },
          { n: 1, title: "RAG voice retrieval", state: "done", note: "Active since you connected · indexes Sent folder for tone cues" },
          { n: 2, title: "LoRA fine-tune (in progress)", state: "active", note: "184 / 200 messages — drafts feel rougher until this finishes" },
          { n: 3, title: "Full pipeline", state: "locked", note: "Unlocks after 200+ Sent messages — confidence indicators disappear" },
        ].map((t, i) => (
          <div key={t.n} style={{
            display: "flex", alignItems: "flex-start", gap: 16,
            padding: "16px 20px",
            borderTop: i > 0 ? "1px solid var(--line)" : "none",
            opacity: t.state === "locked" ? 0.55 : 1,
          }}>
            <span style={{
              width: 22, height: 22, borderRadius: 999, flexShrink: 0, marginTop: 1,
              border: `1.5px solid ${t.state === "done" ? "var(--accent)" : t.state === "active" ? "var(--accent)" : "var(--line-strong)"}`,
              background: t.state === "done" ? "var(--accent)" : t.state === "active" ? "transparent" : "transparent",
              color: "var(--accent-ink)",
              display: "grid", placeItems: "center",
              fontFamily: "var(--font-mono)", fontSize: 10, fontWeight: 500,
            }}>
              {t.state === "done"
                ? <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><path d="m5 12 5 5L20 7" /></svg>
                : t.state === "active"
                  ? <span style={{ width: 8, height: 8, background: "var(--accent)", borderRadius: 999, animation: "pulse 1s infinite var(--ease)" }} />
                  : t.n}
            </span>
            <div style={{ flex: 1 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontSize: 14.5, fontWeight: 500 }}>Tier {t.n}: {t.title}</span>
                {t.state === "active" && <Chip tone="warm">training</Chip>}
              </div>
              <div style={{ fontSize: 12.5, color: "var(--ink-3)", marginTop: 3 }}>{t.note}</div>
            </div>
          </div>
        ))}
      </div>

      <div style={{
        marginTop: 24, padding: 16,
        background: "var(--accent-soft)",
        border: "1px solid var(--accent-line)",
        borderRadius: 8,
        display: "flex", alignItems: "flex-start", gap: 12,
      }}>
        <span style={{ color: "var(--accent)", marginTop: 1 }}>{Icons.bell}</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13.5, color: "var(--ink)", fontWeight: 500, marginBottom: 2 }}>
            We'll let you know when Tier 2 finishes.
          </div>
          <div style={{ fontSize: 12.5, color: "var(--ink-2)" }}>
            A small banner in the side panel will read "Your MailMind just leveled up." That's the only nudge you'll get.
          </div>
        </div>
      </div>
    </div>
  );
}

function BigTierBadge({ tier }) {
  return (
    <div style={{
      width: 72, height: 72, borderRadius: 18,
      background: "var(--surface-2)",
      border: "1px solid var(--line)",
      display: "grid", placeItems: "center",
      position: "relative",
    }}>
      <svg width="72" height="72" viewBox="0 0 72 72" style={{ position: "absolute", inset: 0 }}>
        <circle cx="36" cy="36" r="30" fill="none" stroke="var(--surface-3)" strokeWidth="3" />
        <circle cx="36" cy="36" r="30" fill="none" stroke="var(--accent)" strokeWidth="3"
                strokeDasharray={`${(tier/3) * 188} 999`}
                strokeLinecap="round"
                transform="rotate(-90 36 36)" />
      </svg>
      <div style={{ position: "relative", textAlign: "center" }}>
        <div className="mono" style={{ fontSize: 9, color: "var(--ink-4)", letterSpacing: "0.06em", textTransform: "uppercase" }}>tier</div>
        <div className="display tnum" style={{ fontSize: 22, fontWeight: 500, letterSpacing: "-0.02em", lineHeight: 1 }}>{tier}<span style={{ color: "var(--ink-4)", fontSize: 13, fontWeight: 400 }}>/3</span></div>
      </div>
    </div>
  );
}

// ---------- Account & settings ----------
function SettingsScreen() {
  const [voiceAutoReplay, setVAR] = useS_de(true);
  const [browserNotif, setBN] = useS_de(false);
  const [autoArchive, setAA] = useS_de(true);
  const [confirmSend, setCS] = useS_de(true);

  return (
    <div style={{ maxWidth: 880, margin: "0 auto", padding: "40px 32px 64px" }}>
      <Breadcrumb items={["Account", "Settings"]} />
      <div className="display" style={{ fontSize: 32, fontWeight: 500, letterSpacing: "-0.025em", margin: "8px 0 32px" }}>
        Account & settings
      </div>

      {/* Connected account */}
      <SettingsBlock title="Connected account">
        <div style={{
          display: "flex", alignItems: "center", gap: 14,
          padding: "14px 16px",
          background: "var(--surface)", border: "1px solid var(--line)",
          borderRadius: 8,
        }}>
          <Avatar name="Maya Chen" size={40} hue={210} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 14, fontWeight: 500 }}>maya@northgrid.co</div>
            <div className="mono" style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 2 }}>connected · 14 May 2026 · read · send · modify</div>
          </div>
          <Button kind="secondary">Manage scopes</Button>
        </div>
      </SettingsBlock>

      {/* Voice */}
      <SettingsBlock title="Voice & calibration">
        <SettingRow
          label="Voice calibration sample"
          subtitle="38-second sample captured on 14 May. Re-record to refresh."
          right={<Button kind="secondary">Re-record</Button>}
        />
        <SettingRow
          label="Auto-replay voice responses"
          subtitle="Play assistant replies out loud during voice sessions."
          right={<Toggle value={voiceAutoReplay} onChange={setVAR} />}
        />
      </SettingsBlock>

      {/* Behavior */}
      <SettingsBlock title="Agent behavior">
        <SettingRow
          label="Always confirm before sending"
          subtitle="Recommended — keeps MailMind from sending anything without your tap."
          right={<Toggle value={confirmSend} onChange={setCS} />}
        />
        <SettingRow
          label="Auto-archive low-priority newsletters"
          subtitle="MailMind archives newsletters after 7 days; you can undo from the log."
          right={<Toggle value={autoArchive} onChange={setAA} />}
        />
        <SettingRow
          label="Chrome notifications for Monday digest"
          subtitle="A single toast at 9:00 your local time."
          right={<Toggle value={browserNotif} onChange={setBN} />}
        />
      </SettingsBlock>

      {/* Data */}
      <SettingsBlock title="Data">
        <SettingRow
          label="Export your data"
          subtitle="Download a JSON archive of your commitments, action log, and learned style cues."
          right={<Button kind="secondary">Export…</Button>}
        />
        <SettingRow
          label="Disconnect account"
          subtitle="Stops all access immediately. Deletes your tuned model and embeddings within 24 hours."
          right={<Button kind="primary" danger>Disconnect…</Button>}
        />
      </SettingsBlock>
    </div>
  );
}

function SettingsBlock({ title, children }) {
  return (
    <div style={{ marginBottom: 32 }}>
      <div className="mono" style={{
        fontSize: 10, letterSpacing: "0.08em", textTransform: "uppercase",
        color: "var(--ink-4)", marginBottom: 8, padding: "0 4px",
      }}>{title}</div>
      <div style={{
        background: "var(--surface)", border: "1px solid var(--line)",
        borderRadius: 8, overflow: "hidden",
      }}>
        {children}
      </div>
    </div>
  );
}

function SettingRow({ label, subtitle, right }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 24,
      padding: "16px 18px",
      borderTop: "1px solid var(--line)",
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 3 }}>{label}</div>
        <div style={{ fontSize: 12.5, color: "var(--ink-3)", lineHeight: 1.5 }}>{subtitle}</div>
      </div>
      <div style={{ flexShrink: 0 }}>{right}</div>
    </div>
  );
}

function Toggle({ value, onChange }) {
  return (
    <button onClick={() => onChange(!value)} style={{
      width: 36, height: 22, borderRadius: 999,
      background: value ? "var(--accent)" : "var(--line-strong)",
      border: "none", padding: 2, cursor: "pointer",
      display: "flex", alignItems: "center",
      transition: "background var(--dur-fast) var(--ease)",
    }}>
      <span style={{
        width: 18, height: 18, borderRadius: 999,
        background: "white", boxShadow: "0 1px 3px rgba(0,0,0,0.18)",
        transform: `translateX(${value ? 14 : 0}px)`,
        transition: "transform var(--dur-fast) var(--ease)",
      }} />
    </button>
  );
}

function Breadcrumb({ items }) {
  return (
    <div className="mono" style={{ fontSize: 11, color: "var(--ink-4)", letterSpacing: "0.04em", marginBottom: 8 }}>
      {items.map((it, i) => (
        <span key={i}>{i > 0 && " / "}{it}</span>
      ))}
    </div>
  );
}

// ---------- Action audit log (full) ----------
function AuditLog() {
  const entries = useM_de(() => [
    { day: "Today, May 13", items: [
      { v: "Drafted reply", t: "Re: Q3 OKR review — pricing experiment", time: "10:14", thread: true, reversible: false, op: "open" },
      { v: "Archived", t: "Linear Updates · Weekly digest", time: "10:12", reversible: true },
      { v: "Labeled", t: "Stripe invoice → FYI", time: "10:06", reversible: true },
      { v: "Snoozed", t: "Notion Team — What's new", time: "10:02", reversible: true, until: "Monday 9am" },
      { v: "Marked complete", t: "Commitment: send Q2 readout to Daniel", time: "9:42", reversible: true },
    ]},
    { day: "Yesterday, May 12", items: [
      { v: "Drafted reply", t: "Re: Acme renewal — indemnity clause", time: "16:31", reversible: false, op: "open" },
      { v: "Created commitment", t: "Reply to Helix Health by Wed", time: "14:18", reversible: true, source: "your message to Ana" },
      { v: "Archived", t: "8 newsletters auto-archived", time: "09:00", count: 8, reversible: true },
    ]},
    { day: "Sun, May 11", items: [
      { v: "Sent", t: "Re: Weekly product review — moved to Tuesday", time: "20:14", reversible: false, you: true },
      { v: "Labeled", t: "GitHub PR #4821 → Action", time: "15:02", reversible: true },
    ]},
  ], []);

  const [filter, setFilter] = useS_de("all");

  return (
    <div style={{ maxWidth: 880, margin: "0 auto", padding: "40px 32px 64px" }}>
      <Breadcrumb items={["Account", "Action history"]} />
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", marginBottom: 24 }}>
        <div>
          <div className="display" style={{ fontSize: 32, fontWeight: 500, letterSpacing: "-0.025em", margin: "8px 0 6px" }}>
            Everything we've done for you
          </div>
          <p style={{ color: "var(--ink-3)", fontSize: 14, margin: 0, maxWidth: 520 }}>
            Every action MailMind has taken on your behalf, with one-click reversal where the platform allows.
          </p>
        </div>
        <Button kind="secondary" icon={Icons.filter}>Export</Button>
      </div>

      <div style={{ display: "flex", gap: 6, marginBottom: 18, flexWrap: "wrap" }}>
        {["all", "drafted", "archived", "labeled", "snoozed", "sent", "commitments"].map(f => (
          <button key={f} onClick={() => setFilter(f)} style={{
            padding: "5px 11px", borderRadius: 999,
            background: filter === f ? "var(--ink)" : "var(--surface)",
            color: filter === f ? "var(--bg)" : "var(--ink-2)",
            border: `1px solid ${filter === f ? "var(--ink)" : "var(--line)"}`,
            fontSize: 12, cursor: "pointer", fontFamily: "var(--font-sans)",
            textTransform: "capitalize",
          }}>{f}</button>
        ))}
      </div>

      {entries.map((day, di) => (
        <div key={di} style={{ marginBottom: 28 }}>
          <div className="mono" style={{
            fontSize: 10, letterSpacing: "0.08em", textTransform: "uppercase",
            color: "var(--ink-4)", marginBottom: 8, padding: "0 4px",
          }}>{day.day}</div>
          <div style={{
            background: "var(--surface)", border: "1px solid var(--line)",
            borderRadius: 8, overflow: "hidden",
          }}>
            {day.items.map((a, i) => (
              <div key={i} style={{
                display: "flex", alignItems: "flex-start", gap: 14,
                padding: "12px 16px",
                borderTop: i > 0 ? "1px solid var(--line)" : "none",
              }}>
                <span className="mono tnum" style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 3, width: 38, flexShrink: 0 }}>{a.time}</span>
                <span style={{ color: a.you ? "var(--warning)" : "var(--accent)", marginTop: 1 }}>
                  {a.you ? Icons.send : a.v.startsWith("Drafted") ? Icons.edit : a.v.startsWith("Archived") ? Icons.archive : a.v.startsWith("Labeled") ? Icons.label : a.v.startsWith("Snoozed") ? Icons.clock : a.v.startsWith("Sent") ? Icons.send : a.v.startsWith("Created") ? Icons.plus : Icons.check}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13.5 }}>
                    <span style={{ fontWeight: 500 }}>{a.v}</span> — {a.t}
                    {a.count && <Chip tone="outline" style={{ marginLeft: 8 }}>{a.count} threads</Chip>}
                    {a.until && <Chip tone="outline" style={{ marginLeft: 8 }}>until {a.until}</Chip>}
                  </div>
                  {a.source && <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-4)", marginTop: 2 }}>inferred from: {a.source}</div>}
                  {a.you && <div className="mono" style={{ fontSize: 10.5, color: "var(--warning)", marginTop: 2 }}>YOU PRESSED SEND</div>}
                </div>
                <div style={{ flexShrink: 0 }}>
                  {a.reversible ? (
                    <Button kind="ghost" size="sm" icon={Icons.undo}>Undo</Button>
                  ) : a.op === "open" ? (
                    <Button kind="ghost" size="sm" icon={Icons.external}>Open</Button>
                  ) : (
                    <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-4)" }}>final</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

Object.assign(window, { TrainingStatus, SettingsScreen, AuditLog });
