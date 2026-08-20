// MailMind prototype shell — browser chrome + router between scenes + Tweaks.

const { useState: useS, useEffect: useE, useMemo: useM } = React;

// ---------- Browser chrome (neutral, not branded) ----------
function BrowserChrome({ url, children, tweaks }) {
  return (
    <div style={{
      width: "100%", height: "100%",
      background: "var(--bg-elev)",
      display: "flex", flexDirection: "column",
      overflow: "hidden",
    }}>
      {/* Title bar */}
      <div style={{
        height: 40, flexShrink: 0,
        background: "var(--surface-2)",
        borderBottom: "1px solid var(--line)",
        display: "flex", alignItems: "center", padding: "0 12px", gap: 10,
      }}>
        <div style={{ display: "flex", gap: 6 }}>
          {["#e06c5e", "#e6b340", "#3fb950"].map((c, i) => (
            <span key={i} style={{ width: 11, height: 11, borderRadius: 999, background: c, opacity: 0.85 }} />
          ))}
        </div>
        {/* Tabs */}
        <div style={{ display: "flex", gap: 2, marginLeft: 8, height: 28 }}>
          <BrowserTab active label={url.startsWith("mail") ? "Mailbox" : "MailMind"} />
          <BrowserTab label="docs · roadmap.pdf" />
          <BrowserTab label="northgrid.atlassian" />
        </div>
        <div style={{ flex: 1 }} />
        <div style={{ display: "flex", gap: 6, color: "var(--ink-4)" }}>
          <span>{Icons.user}</span>
        </div>
      </div>
      {/* URL bar */}
      <div style={{
        height: 38, flexShrink: 0,
        borderBottom: "1px solid var(--line)",
        display: "flex", alignItems: "center",
        padding: "0 12px", gap: 8,
        background: "var(--bg-elev)",
      }}>
        <div style={{ display: "flex", gap: 4, color: "var(--ink-4)" }}>
          <button style={{ background: "transparent", border: "none", color: "inherit", padding: 4, display: "flex", cursor: "pointer" }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6"/></svg>
          </button>
          <button style={{ background: "transparent", border: "none", color: "inherit", padding: 4, display: "flex", cursor: "pointer", opacity: 0.5 }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="m9 6 6 6-6 6"/></svg>
          </button>
        </div>
        <div style={{
          flex: 1, height: 26, borderRadius: 13,
          background: "var(--surface)", border: "1px solid var(--line)",
          display: "flex", alignItems: "center", padding: "0 12px",
          fontSize: 12, color: "var(--ink-3)", gap: 6,
        }}>
          <span style={{ color: "var(--success)" }}>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>
          </span>
          <span className="mono" style={{ fontSize: 11.5 }}>{url}</span>
        </div>
        {/* MailMind extension icon — pinned */}
        <div style={{
          display: "flex", alignItems: "center", gap: 4,
          padding: "3px 6px", borderRadius: 6,
          background: "var(--accent-soft)", border: "1px solid var(--accent-line)",
        }} title="MailMind extension">
          <div style={{
            width: 16, height: 16, borderRadius: 4,
            background: "var(--ink)", color: "var(--bg)",
            display: "grid", placeItems: "center",
            fontFamily: "var(--font-display)", fontWeight: 500,
            fontStyle: "italic", fontSize: 10, letterSpacing: "-0.02em",
          }}>M</div>
        </div>
      </div>
      {/* Content */}
      <div style={{ flex: 1, display: "flex", overflow: "hidden", position: "relative" }}>
        {children}
      </div>
    </div>
  );
}

function BrowserTab({ label, active }) {
  return (
    <div style={{
      height: 28, padding: "0 12px",
      display: "flex", alignItems: "center", gap: 6,
      background: active ? "var(--bg-elev)" : "transparent",
      borderRadius: "8px 8px 0 0",
      borderTop: active ? "1px solid var(--line)" : "none",
      borderLeft: active ? "1px solid var(--line)" : "none",
      borderRight: active ? "1px solid var(--line)" : "none",
      fontSize: 11.5, color: active ? "var(--ink)" : "var(--ink-3)",
      cursor: "pointer", position: "relative", top: 1,
      maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
    }}>
      <span style={{
        width: 10, height: 10, borderRadius: 2,
        background: active ? "var(--accent)" : "var(--line-strong)",
      }} />
      {label}
    </div>
  );
}

// ---------- Scene selector (the bottom strip) ----------
function SceneStrip({ scene, setScene, scenes }) {
  const groups = useM(() => {
    const g = {};
    scenes.forEach(s => { (g[s.group] = g[s.group] || []).push(s); });
    return g;
  }, [scenes]);
  return (
    <div style={{
      position: "fixed", left: "50%", bottom: 16,
      transform: "translateX(-50%)",
      background: "color-mix(in oklab, var(--bg-elev) 95%, transparent)",
      backdropFilter: "blur(10px)",
      border: "1px solid var(--line)",
      borderRadius: 14,
      padding: 5, display: "flex", gap: 6, alignItems: "center",
      zIndex: 90, boxShadow: "var(--shadow-lg)",
      maxWidth: "calc(100vw - 32px)",
      overflowX: "auto",
    }} className="thin-scroll">
      {Object.entries(groups).map(([group, items], gi) => (
        <React.Fragment key={group}>
          {gi > 0 && <span style={{ width: 1, height: 18, background: "var(--line)", flexShrink: 0 }} />}
          <span className="mono" style={{ fontSize: 9.5, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--ink-4)", padding: "0 6px", flexShrink: 0 }}>
            {group}
          </span>
          {items.map(s => (
            <button key={s.id} onClick={() => setScene(s.id)} style={{
              background: scene === s.id ? "var(--ink)" : "transparent",
              color: scene === s.id ? "var(--bg)" : "var(--ink-2)",
              border: "none",
              padding: "6px 10px",
              borderRadius: 999,
              fontSize: 11.5, fontFamily: "var(--font-sans)",
              fontWeight: scene === s.id ? 500 : 400,
              cursor: "pointer", whiteSpace: "nowrap",
              display: "inline-flex", alignItems: "center", gap: 5,
              transition: "all var(--dur-fast) var(--ease)",
              flexShrink: 0,
            }}>
              <span className="mono" style={{ fontSize: 9.5, opacity: 0.5 }}>{s.n}</span>
              {s.label}
            </button>
          ))}
        </React.Fragment>
      ))}
    </div>
  );
}

// ---------- The scenes ----------
const SCENES = [
  // — Onboarding —
  { id: "dash_signin",         n: "01", label: "Sign-in",            kind: "dash", screen: "signin",       group: "Onboarding" },
  { id: "dash_oauth_picker",   n: "02", label: "OAuth · accounts",   kind: "dash", screen: "oauth_chooser", group: "Onboarding" },
  { id: "dash_oauth_consent",  n: "03", label: "OAuth · permissions", kind: "dash", screen: "oauth_permissions", group: "Onboarding" },
  { id: "dash_connect",        n: "04", label: "Connecting",         kind: "dash", screen: "connecting",   group: "Onboarding" },
  { id: "dash_voice",          n: "05", label: "Voice calibration",  kind: "dash", screen: "calibrate",    group: "Onboarding" },
  // — Daily driver —
  { id: "sp_out",      n: "06", label: "Side panel · signed out",     kind: "sp",   state: "signedOut",   webmail: "blank",  group: "Daily" },
  { id: "sp_notab",    n: "07", label: "Side panel · no tab",         kind: "sp",   state: "noTab",       webmail: "blank",  group: "Daily" },
  { id: "sp_inbox",    n: "08", label: "Inbox + side panel",          kind: "sp",   state: "inbox",       webmail: "inbox",  group: "Daily" },
  { id: "sp_reading",  n: "09", label: "Reading a thread",            kind: "sp",   state: "reading",     webmail: "thread", group: "Daily" },
  { id: "sp_voice",    n: "10", label: "Voice session",               kind: "sp",   state: "voice",       webmail: "thread", group: "Daily" },
  { id: "sp_drafting", n: "11", label: "Drafting · 3 variants",       kind: "sp",   state: "drafting",    webmail: "thread", group: "Daily" },
  { id: "sp_live",     n: "11b", label: "Drafting · live (animated)", kind: "sp",   state: "liveDraft",   webmail: "thread", group: "Daily" },
  { id: "sp_compose_chat", n: "11c", label: "Compose · chat to draft", kind: "sp",   state: "composeChat", webmail: "inbox",  group: "Daily" },
  { id: "sp_attach",   n: "12", label: "Attachment Q&A",              kind: "sp",   state: "attachment",  webmail: "thread", group: "Daily" },
  { id: "sp_taken",    n: "13", label: "Action just taken",           kind: "sp",   state: "actionTaken", webmail: "thread", group: "Daily" },
  // — Web app supporting —
  { id: "dash_training", n: "14", label: "Training status",  kind: "dash", screen: "training", group: "Dashboard" },
  { id: "dash_settings", n: "15", label: "Account & settings", kind: "dash", screen: "settings", group: "Dashboard" },
  { id: "dash_audit",    n: "16", label: "Action audit log",   kind: "dash", screen: "audit",    group: "Dashboard" },
  // — Edge states —
  { id: "sp_tier1",      n: "17", label: "Tier 1 — learning",      kind: "sp", state: "tier1",       webmail: "inbox",  group: "Edges" },
  { id: "sp_overdue",    n: "18", label: "Overdue commitments",    kind: "sp", state: "overdue",     webmail: "inbox",  group: "Edges" },
  { id: "sp_search",     n: "19", label: "Semantic search",        kind: "sp", state: "search",      webmail: "inbox",  group: "Edges" },
  { id: "sp_digest_empty", n: "20", label: "Empty digest (quiet)", kind: "sp", state: "digestEmpty", webmail: "blank",  group: "Edges" },
  { id: "sp_error",      n: "21", label: "Backend error (offline)", kind: "sp", state: "error",      webmail: "inbox",  group: "Edges" },
];

// ---------- App ----------
function MailMindApp() {
  const tDefaults = window.__TWEAKS_DEFAULTS || { theme: "light", panelWidth: 440 };
  const [t, setTweak] = useTweaks(tDefaults);
  const [sceneId, setSceneId] = useS("sp_inbox");
  const [spState, setSpState] = useS(null); // override per-scene
  const [webmailView, setWebmailView] = useS(null);

  const scene = SCENES.find(s => s.id === sceneId) || SCENES[0];

  // theme
  useE(() => {
    document.documentElement.setAttribute("data-theme", t.theme);
    document.body.setAttribute("data-theme", t.theme);
  }, [t.theme]);

  // Reset overrides on scene change
  useE(() => {
    setSpState(scene.state || null);
    setWebmailView(scene.webmail || null);
  }, [sceneId]);

  const isDash = scene.kind === "dash";

  // Sync URL display
  const url = isDash
    ? `mailmind.app/${scene.screen === "signin" ? "" : scene.screen}`
    : "mail.example.com/u/0/inbox";

  return (
    <>
      <BrowserChrome url={url}>
        {isDash ? (
          <Dashboard
            screen={scene.screen}
            setScreen={(s) => {
              const next = SCENES.find(x => x.kind === "dash" && x.screen === s);
              if (next) setSceneId(next.id);
            }}
            onComplete={() => setSceneId("sp_inbox")}
          />
        ) : (
          <>
            <Webmail
              data={window.DEMO}
              view={webmailView === "blank" ? null : webmailView}
              onOpen={() => { setWebmailView("thread"); setSpState("reading"); setSceneId("sp_reading"); }}
              onBack={() => { setWebmailView("inbox"); setSpState("inbox"); setSceneId("sp_inbox"); }}
              onCompose={() => { setSpState("composeChat"); setSceneId("sp_compose_chat"); }}
            />
            {webmailView === "blank" && <BlankWebmailOverlay />}
            <SidePanel
              state={spState || "inbox"}
              setState={(s) => {
                setSpState(s);
                // also sync scene for known states
                const target = SCENES.find(x => x.kind === "sp" && x.state === s);
                if (target) setSceneId(target.id);
              }}
              data={window.DEMO}
              width={t.panelWidth}
              theme={t.theme}
            />
          </>
        )}
      </BrowserChrome>

      <SceneStrip scene={sceneId} setScene={setSceneId} scenes={SCENES} />

      <TweaksPanel title="Tweaks">
        <TweakSection label="Appearance">
          <TweakRadio
            label="Theme"
            value={t.theme}
            options={[
              { value: "light", label: "Light" },
              { value: "dark",  label: "Dark"  },
            ]}
            onChange={v => setTweak("theme", v)}
          />
        </TweakSection>
        <TweakSection label="Side panel">
          <TweakRadio
            label="Width"
            value={t.panelWidth}
            options={[
              { value: 400, label: "400" },
              { value: 440, label: "440" },
              { value: 480, label: "480" },
            ]}
            onChange={v => setTweak("panelWidth", v)}
          />
        </TweakSection>
        <TweakSection label="Jump to scene">
          <div style={{ display: "flex", flexDirection: "column", gap: 2, maxHeight: 360, overflowY: "auto" }} className="thin-scroll">
            {Object.entries(SCENES.reduce((acc, s) => { (acc[s.group] = acc[s.group] || []).push(s); return acc; }, {})).map(([group, items]) => (
              <React.Fragment key={group}>
                <div className="mono" style={{ fontSize: 9.5, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--ink-4)", padding: "8px 4px 4px" }}>{group}</div>
                {items.map(s => (
                  <button key={s.id} onClick={() => setSceneId(s.id)} style={{
                    textAlign: "left", padding: "5px 8px", borderRadius: 4,
                    background: sceneId === s.id ? "var(--accent-soft)" : "transparent",
                    color: sceneId === s.id ? "var(--accent)" : "var(--ink-2)",
                    border: "1px solid transparent",
                    fontSize: 12, cursor: "pointer", fontFamily: "var(--font-sans)",
                    display: "flex", gap: 8, alignItems: "center",
                  }}>
                    <span className="mono" style={{ fontSize: 10, color: "var(--ink-4)", width: 18 }}>{s.n}</span>
                    <span>{s.label}</span>
                  </button>
                ))}
              </React.Fragment>
            ))}
          </div>
        </TweakSection>
      </TweaksPanel>
    </>
  );
}

function BlankWebmailOverlay() {
  return (
    <div style={{
      flex: 1, height: "100%",
      background: "var(--bg)",
      display: "grid", placeItems: "center",
      color: "var(--ink-4)",
      borderRight: "0",
    }}>
      <div style={{ textAlign: "center", maxWidth: 360 }}>
        <div className="mono" style={{ fontSize: 10, letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 8 }}>
          No mail tab focused
        </div>
        <div style={{ fontSize: 13, color: "var(--ink-3)", lineHeight: 1.5 }}>
          The side panel works on its own — recent mail, debt, and commitments — even when you're not in your inbox tab.
        </div>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<MailMindApp />);
