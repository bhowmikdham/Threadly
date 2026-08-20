// Web Dashboard screens: Sign-in, OAuth connecting, Tier 0 voice calibration.

const { useState: useState_db, useEffect: useEffect_db, useRef: useRef_db } = React;

function Dashboard({ screen, setScreen, onComplete }) {
  return (
    <div style={{
      width: "100%", height: "100%",
      background: "var(--bg)", color: "var(--ink)",
      display: "flex", flexDirection: "column",
      overflow: "hidden",
    }}>
      {screen === "oauth_chooser" || screen === "oauth_permissions" ? null : <DashboardHeader />}
      <div style={{ flex: 1, overflowY: "auto" }} className="thin-scroll">
        {screen === "signin"     && <SignIn onConnect={() => setScreen("oauth_chooser")} />}
        {screen === "oauth_chooser"   && <OAuthFlow step="provider"    setStep={s => setScreen(s === "connecting" ? "connecting" : s === "signin" ? "signin" : "oauth_permissions")} />}
        {screen === "oauth_permissions" && <OAuthFlow step="permissions" setStep={s => setScreen(s === "connecting" ? "connecting" : s === "signin" ? "signin" : "oauth_chooser")} />}
        {screen === "connecting" && <Connecting onDone={() => setScreen("calibrate")} />}
        {screen === "calibrate"  && <Calibrate onDone={onComplete} />}
        {screen === "training"   && <TrainingStatus />}
        {screen === "settings"   && <SettingsScreen />}
        {screen === "audit"      && <AuditLog />}
      </div>
    </div>
  );
}

function DashboardHeader() {
  return (
    <div style={{
      height: 64, flexShrink: 0,
      borderBottom: "1px solid var(--line)",
      display: "flex", alignItems: "center",
      padding: "0 32px", justifyContent: "space-between",
      background: "var(--bg-elev)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{
          width: 28, height: 28, borderRadius: 7,
          background: "var(--ink)", color: "var(--bg)",
          display: "grid", placeItems: "center",
          fontFamily: "var(--font-display)", fontWeight: 500,
          fontStyle: "italic", fontSize: 16, letterSpacing: "-0.02em",
        }}>M</div>
        <div className="display" style={{ fontSize: 18, fontWeight: 500, letterSpacing: "-0.01em" }}>MailMind</div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 18, fontSize: 13, color: "var(--ink-3)" }}>
        <a style={{ color: "inherit", cursor: "pointer" }}>How it works</a>
        <a style={{ color: "inherit", cursor: "pointer" }}>Privacy</a>
        <a style={{ color: "inherit", cursor: "pointer" }}>Pricing</a>
      </div>
    </div>
  );
}

// ---------- Sign-in / marketing ----------
function SignIn({ onConnect }) {
  return (
    <div style={{
      maxWidth: 980, margin: "0 auto", padding: "72px 32px 80px",
    }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 64, alignItems: "center" }}>
        <div>
          <Chip tone="outline" style={{ marginBottom: 20 }}>
            <span style={{ width: 5, height: 5, borderRadius: 999, background: "var(--accent)" }} />
            Quietly available, early 2026
          </Chip>
          <h1 className="display" style={{
            fontSize: 56, lineHeight: 1.02,
            fontWeight: 400, letterSpacing: "-0.025em",
            margin: "0 0 22px", color: "var(--ink)",
            textWrap: "pretty",
          }}>
            A quieter way to<br/>
            <em style={{ fontStyle: "italic", color: "var(--accent)" }}>actually</em> finish your email.
          </h1>
          <p style={{
            fontSize: 16.5, lineHeight: 1.55, color: "var(--ink-2)",
            margin: "0 0 32px", maxWidth: 480,
          }}>
            MailMind sits beside your inbox, reads what you're reading, and writes in your voice. It tracks what you've promised. It triages what to reply. It never sends without asking.
          </p>
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <Button kind="primary" size="lg" onClick={onConnect}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                Connect your inbox
                <span>{Icons.arrow}</span>
              </span>
            </Button>
            <Button kind="ghost" size="lg">Read the manifesto</Button>
          </div>
          <div className="mono" style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 18, letterSpacing: "0.02em" }}>
            Read & write via Google OAuth · revoke any time · no training on your data
          </div>
        </div>

        {/* Right side: a quiet "preview" graphic */}
        <PreviewFrame />
      </div>

      <FeatureGrid />
    </div>
  );
}

function PreviewFrame() {
  return (
    <div style={{
      background: "var(--surface)",
      border: "1px solid var(--line)",
      borderRadius: "var(--r-lg)",
      padding: 18,
      boxShadow: "var(--shadow-lg)",
      position: "relative",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
        <span style={{ width: 8, height: 8, borderRadius: 999, background: "var(--line-strong)" }} />
        <span style={{ width: 8, height: 8, borderRadius: 999, background: "var(--line-strong)" }} />
        <span style={{ width: 8, height: 8, borderRadius: 999, background: "var(--line-strong)" }} />
        <div style={{ flex: 1 }} />
        <TierPill tier={2} total={3} label="Training" />
      </div>

      <Card padding={12}>
        <div className="mono" style={{ fontSize: 10, color: "var(--ink-4)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 8 }}>Email debt</div>
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between" }}>
          <div>
            <div className="display tnum" style={{ fontSize: 32, fontWeight: 400, lineHeight: 1 }}>23</div>
            <div style={{ fontSize: 11.5, color: "var(--ink-4)", marginTop: 4 }}>owed by you</div>
          </div>
          <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 32 }}>
            {[9,12,8,15,18,21,23].map((v, i) => (
              <div key={i} style={{
                width: 5, height: `${(v/23)*100}%`,
                background: i === 6 ? "var(--accent)" : "var(--line-strong)",
                borderRadius: 2,
              }} />
            ))}
          </div>
        </div>
      </Card>
      <div style={{ height: 10 }} />
      <Card padding={12}>
        <div className="mono" style={{ fontSize: 10, color: "var(--ink-4)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 8 }}>Open commitments</div>
        {[
          { t: "Send pricing notes to Priya", to: "tomorrow" },
          { t: "Reply on Acme indemnity clause", to: "Fri" },
          { t: "Approve Jordan onboarding plan", to: "May 18" },
        ].map((c, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0", borderTop: i > 0 ? "1px solid var(--line)" : "none" }}>
            <span style={{ width: 14, height: 14, borderRadius: 999, border: "1.5px solid var(--line-strong)" }} />
            <span style={{ flex: 1, fontSize: 12.5 }}>{c.t}</span>
            <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-4)" }}>{c.to}</span>
          </div>
        ))}
      </Card>
      <div style={{ height: 10 }} />
      <div style={{ display: "flex", gap: 8 }}>
        <Chip tone="warm">reply</Chip>
        <Chip tone="cool">fyi</Chip>
        <Chip tone="muted">newsletter</Chip>
      </div>
    </div>
  );
}

function FeatureGrid() {
  const items = [
    { k: "Tracks what you've promised", v: "Cross-thread commitment memory, with due dates and one-click complete." },
    { k: "Triages at a glance", v: "Every thread tagged reply / optional / fyi / newsletter. Override to teach." },
    { k: "Drafts in your voice", v: "Tone-matched to your Sent folder. Three variants. You always pick." },
    { k: "Acts only with your say-so", v: "Reversible actions are one click. Send always confirms." },
    { k: "Voice when you want it", v: "Push-to-talk. Never ambient. Grounded in the thread you're reading." },
    { k: "Quiet by design", v: "Lives in the side panel. Doesn't replace your inbox or fight for attention." },
  ];
  return (
    <div style={{ marginTop: 96, paddingTop: 56, borderTop: "1px solid var(--line)" }}>
      <SectionLabel>Six things, done well</SectionLabel>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(3, 1fr)",
        gap: 32, marginTop: 16,
      }}>
        {items.map((it, i) => (
          <div key={i}>
            <div className="display" style={{
              fontSize: 18, fontWeight: 500, marginBottom: 6,
              letterSpacing: "-0.01em",
            }}>{it.k}</div>
            <div style={{ fontSize: 13.5, color: "var(--ink-3)", lineHeight: 1.55 }}>{it.v}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------- OAuth connecting state ----------
function Connecting({ onDone }) {
  const [phase, setPhase] = useState_db(0);
  // 0: connecting, 1: read mail metadata, 2: index sent folder, 3: done
  useEffect_db(() => {
    const t1 = setTimeout(() => setPhase(1), 900);
    const t2 = setTimeout(() => setPhase(2), 2000);
    const t3 = setTimeout(() => setPhase(3), 3200);
    const t4 = setTimeout(() => onDone && onDone(), 4400);
    return () => { [t1,t2,t3,t4].forEach(clearTimeout); };
  }, []);

  const steps = [
    "Connecting to your Gmail account",
    "Reading inbox structure (metadata only)",
    "Indexing your Sent folder for voice baseline",
    "Connected · Ready for calibration",
  ];

  return (
    <div style={{
      maxWidth: 560, margin: "0 auto", padding: "80px 32px",
      textAlign: "center",
    }}>
      <div style={{
        width: 56, height: 56, borderRadius: 999,
        background: phase === 3 ? "var(--success-soft)" : "var(--surface-2)",
        color: phase === 3 ? "var(--success)" : "var(--accent)",
        display: "grid", placeItems: "center",
        margin: "0 auto 24px",
        transition: "all var(--dur-slow) var(--ease)",
        position: "relative",
      }}>
        {phase === 3 ? Icons.checkCircle : Icons.shield}
        {phase < 3 && (
          <span style={{
            position: "absolute", inset: -4, borderRadius: 999,
            border: "2px solid var(--accent)", opacity: 0.3,
            animation: "pulse 1.4s infinite var(--ease)",
          }} />
        )}
      </div>
      <h2 className="display" style={{ fontSize: 28, fontWeight: 400, letterSpacing: "-0.02em", margin: "0 0 10px" }}>
        {phase === 3 ? "Connected." : "Linking your account…"}
      </h2>
      <p style={{ color: "var(--ink-3)", fontSize: 14.5, margin: "0 0 32px" }}>
        {phase === 3 ? "Next: a two-minute voice calibration so MailMind learns your rhythm." : "Hang tight. We only read what you grant; nothing trains a public model."}
      </p>
      <div style={{
        background: "var(--surface)", border: "1px solid var(--line)",
        borderRadius: "var(--r-md)", padding: 18, textAlign: "left",
        maxWidth: 440, margin: "0 auto",
      }}>
        {steps.map((s, i) => (
          <div key={i} style={{
            display: "flex", alignItems: "center", gap: 12,
            padding: "8px 0",
            borderTop: i > 0 ? "1px solid var(--line)" : "none",
            opacity: i > phase ? 0.4 : 1,
            transition: "opacity var(--dur) var(--ease)",
          }}>
            <div style={{
              width: 18, height: 18, borderRadius: 999,
              border: `1.5px solid ${i < phase ? "var(--accent)" : "var(--line-strong)"}`,
              background: i < phase ? "var(--accent)" : "transparent",
              color: "var(--accent-ink)",
              display: "grid", placeItems: "center", flexShrink: 0,
            }}>
              {i < phase && <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><path d="m5 12 5 5L20 7" /></svg>}
              {i === phase && (
                <span style={{
                  width: 8, height: 8, borderRadius: 999, background: "var(--accent)",
                  animation: "pulse 1s infinite var(--ease)",
                }} />
              )}
            </div>
            <span style={{ fontSize: 13.5, color: i <= phase ? "var(--ink)" : "var(--ink-3)" }}>{s}</span>
            {i === phase && i < 3 && (
              <span style={{ marginLeft: "auto", color: "var(--ink-4)" }}><Dots /></span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------- Tier 0 voice calibration ----------
function Calibrate({ onDone }) {
  const [state, setState] = useState_db("idle"); // idle, recording, done
  const [progress, setProgress] = useState_db(0);
  const intervalRef = useRef_db(null);

  const start = () => {
    setState("recording");
    setProgress(0);
    intervalRef.current = setInterval(() => {
      setProgress(p => {
        if (p >= 100) {
          clearInterval(intervalRef.current);
          setState("done");
          return 100;
        }
        return p + 1.4;
      });
    }, 80);
  };

  const stop = () => {
    clearInterval(intervalRef.current);
    setState("done");
  };

  const reset = () => { setProgress(0); setState("idle"); };

  return (
    <div style={{
      maxWidth: 720, margin: "0 auto", padding: "56px 32px 80px",
    }}>
      {/* Step indicator */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 16, marginBottom: 36 }}>
        {[
          { n: 1, label: "Connect", done: true },
          { n: 2, label: "Voice calibration", active: true },
          { n: 3, label: "Open the side panel" },
        ].map((s, i) => (
          <React.Fragment key={i}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{
                width: 22, height: 22, borderRadius: 999,
                border: `1.5px solid ${s.done ? "var(--accent)" : s.active ? "var(--accent)" : "var(--line-strong)"}`,
                background: s.done ? "var(--accent)" : "transparent",
                color: s.done ? "var(--accent-ink)" : s.active ? "var(--accent)" : "var(--ink-4)",
                fontSize: 11, fontWeight: 500, display: "grid", placeItems: "center",
                fontFamily: "var(--font-mono)",
              }}>{s.done ? <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><path d="m5 12 5 5L20 7" /></svg> : s.n}</span>
              <span style={{ fontSize: 13, color: s.active ? "var(--ink)" : "var(--ink-3)", fontWeight: s.active ? 500 : 400 }}>{s.label}</span>
            </div>
            {i < 2 && <span style={{ width: 24, height: 1, background: "var(--line-strong)" }} />}
          </React.Fragment>
        ))}
      </div>

      <div style={{ textAlign: "center", marginBottom: 32 }}>
        <h2 className="display" style={{ fontSize: 32, fontWeight: 400, letterSpacing: "-0.02em", margin: "0 0 12px" }}>
          Read a short paragraph aloud.
        </h2>
        <p style={{ color: "var(--ink-3)", fontSize: 14.5, margin: 0, maxWidth: 520, marginInline: "auto" }}>
          This gives MailMind a starting sense of your rhythm and formality, so your first drafts already sound like you. It takes about a minute.
        </p>
      </div>

      <Card padding={28} style={{ marginBottom: 24 }}>
        <div className="display" style={{
          fontSize: 21, lineHeight: 1.55, fontWeight: 400, color: "var(--ink)",
          fontStyle: "italic",
          textWrap: "balance",
        }}>
          "Most of what I write in email is shorter than it could be. I'm not trying to sound formal — I'm trying to sound clear. When I disagree, I'd rather say so directly and explain why, than soften it into something polite that doesn't actually land. Brevity is a kindness, most of the time."
        </div>
        <div className="mono" style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 16, letterSpacing: "0.04em" }}>
          ~ 35 seconds at a comfortable pace
        </div>
      </Card>

      {/* Mic & waveform */}
      <div style={{
        background: "var(--surface)", border: "1px solid var(--line)",
        borderRadius: "var(--r-lg)", padding: 32,
        display: "flex", flexDirection: "column", alignItems: "center", gap: 20,
      }}>
        <CalibrationWaveform state={state} />

        <button
          onClick={state === "recording" ? stop : state === "done" ? null : start}
          disabled={state === "done"}
          style={{
            position: "relative",
            width: 76, height: 76, borderRadius: 999,
            background: state === "recording" ? "var(--danger)" : state === "done" ? "var(--success)" : "var(--accent)",
            color: "var(--accent-ink)",
            border: "none", cursor: state === "done" ? "default" : "pointer",
            display: "grid", placeItems: "center",
            boxShadow: "var(--shadow-md)",
            transition: "background var(--dur) var(--ease)",
          }}>
          {state === "recording" && <span style={{
            position: "absolute", inset: -6, borderRadius: 999,
            border: "2px solid var(--danger)", opacity: 0.4,
            animation: "pulse 1.2s infinite var(--ease)",
          }} />}
          {state === "done"
            ? <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m5 12 5 5L20 7"/></svg>
            : state === "recording"
            ? <span style={{ width: 22, height: 22, background: "currentColor", borderRadius: 4 }} />
            : <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="3" width="6" height="12" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8"/></svg>}
        </button>

        <div style={{ textAlign: "center" }}>
          <div style={{ fontSize: 14, color: "var(--ink)", marginBottom: 4 }}>
            {state === "idle"     && "Press the mic to begin"}
            {state === "recording" && "Listening… press to stop"}
            {state === "done"     && "Calibration captured."}
          </div>
          <div className="mono" style={{ fontSize: 11, color: "var(--ink-4)" }}>
            {state === "recording" && `${(progress*0.35).toFixed(1)}s · ${Math.round(progress)}%`}
            {state === "done"     && "We'll keep refining this from your Sent folder as you go."}
            {state === "idle"     && "Audio stays on-device until you're done. Nothing uploads until you confirm."}
          </div>
        </div>

        {/* Progress bar */}
        <div style={{
          width: "100%", height: 3, background: "var(--surface-2)",
          borderRadius: 2, overflow: "hidden",
        }}>
          <div style={{
            width: `${progress}%`, height: "100%",
            background: state === "done" ? "var(--success)" : "var(--accent)",
            transition: "width 80ms linear",
          }} />
        </div>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 24, alignItems: "center" }}>
        <Button kind="ghost" onClick={reset}>Re-record</Button>
        <Button kind="primary" size="lg" disabled={state !== "done"} onClick={onDone}>
          Continue to side panel <span style={{ marginLeft: 6 }}>{Icons.arrow}</span>
        </Button>
      </div>
    </div>
  );
}

function CalibrationWaveform({ state }) {
  const bars = Array.from({ length: 54 }, (_, i) => i);
  const [tick, setTick] = useState_db(0);
  useEffect_db(() => {
    const id = setInterval(() => setTick(t => t + 1), 100);
    return () => clearInterval(id);
  }, []);
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "center",
      gap: 4, height: 56, width: "100%",
    }}>
      {bars.map(i => {
        const base = state === "recording"
          ? Math.max(4, Math.abs(Math.sin((tick + i*4)*0.3)) * 32 + Math.sin(i*0.4 + tick*0.2)*6 + 6)
          : state === "done"
          ? Math.max(4, Math.abs(Math.sin(i*0.5)) * 22 + 4)
          : 4;
        return <span key={i} style={{
          width: 4, height: base,
          background: state === "recording" ? "var(--danger)" : state === "done" ? "var(--success)" : "var(--line-strong)",
          borderRadius: 2,
          transition: "height 100ms linear, background var(--dur) var(--ease)",
        }} />;
      })}
    </div>
  );
}

Object.assign(window, { Dashboard });
