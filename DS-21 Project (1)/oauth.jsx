// OAuth flow — generic provider screens, not branded.
// Designed to look like a real OAuth provider's sign-in & permission screens
// without recreating any specific company's branded UI.

const { useState: useS_oa, useEffect: useE_oa } = React;

function OAuthFlow({ step, setStep, onComplete }) {
  return (
    <div style={{
      width: "100%", height: "100%",
      background: step === "provider" ? "var(--surface-2)" : "var(--bg)",
      display: "grid", placeItems: "center",
      padding: 24, overflow: "auto",
    }} className="thin-scroll">
      {step === "provider"   && <ProviderAccountChooser onPick={() => setStep("permissions")} onBack={() => setStep("signin")} />}
      {step === "permissions" && <PermissionConsent onAllow={() => setStep("connecting")} onBack={() => setStep("provider")} />}
    </div>
  );
}

// ---------- Account chooser (looks like a generic provider sign-in) ----------
function ProviderAccountChooser({ onPick, onBack }) {
  const [picked, setPicked] = useS_oa(null);

  const accounts = [
    { name: "Maya Chen", email: "maya@northgrid.co",   hue: 210 },
    { name: "Maya Chen", email: "maya.chen@gmail.com", hue: 30 },
  ];

  return (
    <div style={{
      width: 460, maxWidth: "100%",
      background: "var(--surface)",
      border: "1px solid var(--line)",
      borderRadius: 12,
      boxShadow: "var(--shadow-lg)",
      overflow: "hidden",
    }}>
      <div style={{
        padding: "20px 24px 14px",
        borderBottom: "1px solid var(--line)",
        display: "flex", alignItems: "center", gap: 12,
      }}>
        <div style={{
          width: 28, height: 28, borderRadius: 6,
          background: "var(--surface-2)",
          display: "grid", placeItems: "center",
          color: "var(--ink-2)",
        }}>
          {/* Generic 'envelope' provider mark */}
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="5" width="18" height="14" rx="2" />
            <path d="m3 7 9 6 9-6" />
          </svg>
        </div>
        <div>
          <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-4)", letterSpacing: "0.08em", textTransform: "uppercase" }}>
            accounts.example.com
          </div>
          <div style={{ fontSize: 15, fontWeight: 500, marginTop: 2 }}>Choose an account</div>
        </div>
      </div>

      <div style={{ padding: "12px 18px 18px" }}>
        <div style={{ fontSize: 13, color: "var(--ink-3)", marginBottom: 14 }}>
          to continue to{" "}
          <span style={{ color: "var(--ink), fontWeight: 500" }}>
            <span className="display" style={{ fontWeight: 500 }}>MailMind</span>
          </span>
        </div>

        <div style={{ display: "flex", flexDirection: "column" }}>
          {accounts.map((a, i) => (
            <button key={i} onClick={() => { setPicked(i); setTimeout(onPick, 320); }}
                    style={{
              display: "flex", alignItems: "center", gap: 12,
              padding: "10px 12px", borderRadius: 8,
              background: picked === i ? "var(--accent-soft)" : "transparent",
              border: `1px solid ${picked === i ? "var(--accent-line)" : "transparent"}`,
              cursor: "pointer", textAlign: "left", width: "100%",
              fontFamily: "var(--font-sans)",
              transition: "background var(--dur-fast) var(--ease)",
            }}
            onMouseEnter={e => { if (picked !== i) e.currentTarget.style.background = "var(--surface-2)"; }}
            onMouseLeave={e => { if (picked !== i) e.currentTarget.style.background = "transparent"; }}
            >
              <Avatar name={a.name} size={36} hue={a.hue} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 500, color: "var(--ink)" }}>{a.name}</div>
                <div style={{ fontSize: 12.5, color: "var(--ink-3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.email}</div>
              </div>
              {picked === i && <span style={{ color: "var(--accent)" }}>{Icons.check}</span>}
            </button>
          ))}

          <button style={{
            display: "flex", alignItems: "center", gap: 12,
            padding: "10px 12px", borderRadius: 8,
            background: "transparent", border: "1px solid transparent",
            cursor: "pointer", textAlign: "left", width: "100%",
            color: "var(--ink-2)", fontSize: 13.5,
            fontFamily: "var(--font-sans)",
          }}>
            <span style={{
              width: 36, height: 36, borderRadius: 999,
              background: "var(--surface-2)", color: "var(--ink-3)",
              display: "grid", placeItems: "center",
            }}>{Icons.plus}</span>
            Use another account
          </button>
        </div>

        <div style={{
          marginTop: 14, paddingTop: 14, borderTop: "1px solid var(--line)",
          display: "flex", justifyContent: "space-between", alignItems: "center",
          fontSize: 11, color: "var(--ink-4)",
        }}>
          <button onClick={onBack} style={{
            background: "transparent", border: "none", color: "var(--accent)",
            cursor: "pointer", padding: 0, fontSize: 12, fontFamily: "var(--font-sans)", fontWeight: 500,
          }}>← Back</button>
          <span className="mono">English (US)</span>
        </div>
      </div>
    </div>
  );
}

// ---------- Permission consent screen ----------
function PermissionConsent({ onAllow, onBack }) {
  const [checked, setChecked] = useS_oa({ read: true, send: true, modify: true });

  const scopes = [
    { id: "read",   title: "Read your mail",   detail: "View messages, threads, labels, and metadata. Required for triage, summaries, and commitment tracking." },
    { id: "send",   title: "Send messages on your behalf", detail: "Only after you press Send in a confirmation dialog. MailMind never sends without your explicit click." },
    { id: "modify", title: "Modify labels, archive, snooze", detail: "Lets the agent file, archive, and snooze. Every modification is reversible from the action log." },
  ];

  return (
    <div style={{
      width: 520, maxWidth: "100%",
      background: "var(--surface)",
      border: "1px solid var(--line)",
      borderRadius: 12,
      boxShadow: "var(--shadow-lg)",
      overflow: "hidden",
    }}>
      <div style={{
        padding: "18px 24px",
        borderBottom: "1px solid var(--line)",
        display: "flex", alignItems: "center", gap: 14,
      }}>
        <Avatar name="Maya Chen" size={32} hue={210} />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13.5, color: "var(--ink)", fontWeight: 500 }}>maya@northgrid.co</div>
          <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-4)", marginTop: 2 }}>signed in via provider</div>
        </div>
        <button onClick={onBack} style={{ background: "transparent", border: "none", color: "var(--ink-3)", fontSize: 12, cursor: "pointer", fontFamily: "var(--font-sans)" }}>
          switch account
        </button>
      </div>

      <div style={{ padding: "20px 24px 4px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
          <div style={{
            width: 36, height: 36, borderRadius: 8,
            background: "var(--ink)", color: "var(--bg)",
            display: "grid", placeItems: "center",
            fontFamily: "var(--font-serif)", fontWeight: 500,
            fontStyle: "italic", fontSize: 18, letterSpacing: "-0.02em",
          }}>M</div>
          <div>
            <div style={{ fontSize: 11, color: "var(--ink-4)" }}>MailMind would like access to your inbox</div>
            <div className="display" style={{ fontSize: 18, fontWeight: 500, marginTop: 2 }}>
              Grant access to continue
            </div>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 14 }}>
          {scopes.map(s => (
            <div key={s.id} style={{
              display: "flex", gap: 12,
              padding: 12, borderRadius: 8,
              background: "var(--surface-2)",
              border: "1px solid var(--line)",
            }}>
              <button onClick={() => setChecked(c => ({...c, [s.id]: !c[s.id]}))}
                      disabled={s.id === "read"} /* read is required */
                      style={{
                width: 18, height: 18, borderRadius: 4,
                background: checked[s.id] ? "var(--accent)" : "var(--surface)",
                border: `1.5px solid ${checked[s.id] ? "var(--accent)" : "var(--line-strong)"}`,
                cursor: s.id === "read" ? "default" : "pointer",
                color: "var(--accent-ink)",
                display: "grid", placeItems: "center", flexShrink: 0,
                marginTop: 1,
              }}>
                {checked[s.id] && <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><path d="m5 12 5 5L20 7"/></svg>}
              </button>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13.5, fontWeight: 500, marginBottom: 3 }}>
                  {s.title}
                  {s.id === "read" && <span className="mono" style={{ fontSize: 10, color: "var(--ink-4)", marginLeft: 8 }}>required</span>}
                </div>
                <div style={{ fontSize: 12.5, color: "var(--ink-3)", lineHeight: 1.5 }}>
                  {s.detail}
                </div>
              </div>
            </div>
          ))}
        </div>

        <div style={{
          padding: "10px 12px", borderRadius: 6,
          background: "var(--warning-soft)",
          border: "1px solid color-mix(in oklab, var(--warning) 25%, transparent)",
          fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.5,
          display: "flex", gap: 8, alignItems: "flex-start",
        }}>
          <span style={{ color: "var(--warning)", marginTop: 1 }}>{Icons.shield}</span>
          <span>
            Make sure you trust MailMind. You can revoke access at any time from your account permissions page. We do not train any public model on your data.
          </span>
        </div>
      </div>

      <div style={{
        padding: "16px 24px", display: "flex", gap: 10, justifyContent: "flex-end",
        borderTop: "1px solid var(--line)",
      }}>
        <Button kind="ghost" onClick={onBack}>Cancel</Button>
        <Button kind="primary" onClick={onAllow}>
          Allow {checked.read && checked.send && checked.modify ? "all" : "selected"} access
        </Button>
      </div>
    </div>
  );
}

Object.assign(window, { OAuthFlow });
