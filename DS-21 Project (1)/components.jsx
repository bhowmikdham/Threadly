// Shared MailMind components. Outline icons inline, no library.
// Exported to window at bottom for cross-script access.

const { useState, useRef, useEffect, useCallback, useMemo } = React;

// ---------- Icons (outline only, 1.5px stroke) ----------
// Each icon is rendered from one or more SVG <path d="..."> strings.
const Icon = ({ paths, size = 16, stroke = 1.5, fill = "none", style }) => {
  const arr = typeof paths === "string" ? [paths] : paths;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={fill} stroke="currentColor"
         strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" style={style}>
      {arr.map((p, i) => <path key={i} d={p} />)}
    </svg>
  );
};

const ICON_PATHS = {
  search:      ["M11 4a7 7 0 1 1-4.95 11.95L3 19", "M11 4a7 7 0 0 1 0 14"],
  mic:         ["M9 6a3 3 0 0 1 6 0v6a3 3 0 0 1-6 0V6Z", "M5 11a7 7 0 0 0 14 0", "M12 18v3", "M8 21h8"],
  send:        ["M22 2 11 13", "M22 2l-7 20-4-9-9-4 20-7Z"],
  reply:       ["M9 14 4 9l5-5", "M4 9h10a6 6 0 0 1 6 6v5"],
  archive:     ["M3 5h18v4H3z", "M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9", "M10 13h4"],
  clock:       ["M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18Z", "M12 7v5l3 2"],
  check:       ["m5 12 5 5L20 7"],
  checkCircle: ["M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18Z", "m8 12 3 3 5-6"],
  circle:      ["M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18Z"],
  x:           ["M6 6l12 12", "M18 6 6 18"],
  chevron:     ["m9 6 6 6-6 6"],
  chevronDown: ["m6 9 6 6 6-6"],
  chevronUp:   ["m6 15 6-6 6 6"],
  settings:    ["M12 9a3 3 0 1 1 0 6 3 3 0 0 1 0-6Z", "M12 2v3M12 19v3M5 5l2 2M17 17l2 2M2 12h3M19 12h3M5 19l2-2M17 7l2-2"],
  inbox:       ["M3 13h4l2 3h6l2-3h4", "M3 13l3-8h12l3 8", "M3 13v6a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-6"],
  star:        ["m12 3 2.7 5.7 6.3.9-4.5 4.4 1 6.3L12 17.3 6.5 20.3l1-6.3L3 9.6l6.3-.9L12 3z"],
  paperclip:   ["m21 12-8.5 8.5a5.5 5.5 0 0 1-7.8-7.8L13 4.5A3.5 3.5 0 0 1 18 9.5l-8.5 8.5a1.5 1.5 0 0 1-2.1-2.1L14.5 9"],
  doc:         ["M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z", "M14 3v5h5", "M9 13h6", "M9 17h4"],
  user:        ["M12 4a4 4 0 1 1 0 8 4 4 0 0 1 0-8Z", "M4 21a8 8 0 0 1 16 0"],
  shield:      ["M12 3 4 6v6c0 5 3.5 8.5 8 9 4.5-.5 8-4 8-9V6l-8-3Z"],
  zap:         ["M13 2 4 14h7l-1 8 9-12h-7l1-8Z"],
  undo:        ["M9 14 4 9l5-5", "M4 9h10a6 6 0 0 1 0 12h-3"],
  warning:     ["M12 3 2 21h20L12 3Z", "M12 10v5", "M12 18h.01"],
  info:        ["M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18Z", "M12 8h.01", "M11 12h1v5h1"],
  label:       ["M4 4h11l5 8-5 8H4z"],
  calendar:    ["M5 5h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Z", "M3 10h18", "M8 3v4", "M16 3v4"],
  filter:      ["M4 5h16l-6 8v5l-4 2v-7L4 5Z"],
  plus:        ["M12 5v14", "M5 12h14"],
  trash:       ["M4 7h16", "M10 11v6", "M14 11v6", "M6 7l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13", "M9 7V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v3"],
  more:        ["M5 12h.01", "M12 12h.01", "M19 12h.01"],
  external:    ["M14 4h6v6", "M10 14 20 4", "M19 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h6"],
  arrow:       ["M5 12h14", "M13 5l7 7-7 7"],
  edit:        ["M12 20h9", "M3 20h3l11-11a2 2 0 0 0-3-3L3 17v3Z"],
  bell:        ["M6 19h12l-2-3V11a4 4 0 1 0-8 0v5L6 19Z", "M10 19a2 2 0 0 0 4 0"],
  sparkle:     ["M12 3v6", "M12 15v6", "M3 12h6", "M15 12h6"],
  back:        ["M19 12H5", "M11 5l-7 7 7 7"],
};

const Icons = Object.fromEntries(
  Object.entries(ICON_PATHS).map(([k, paths]) => [k, <Icon key={k} paths={paths} />])
);

// ---------- Buttons ----------
const Button = ({ kind = "secondary", size = "md", icon, children, onClick, disabled, style, full, danger, title }) => {
  const base = {
    display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6,
    border: "1px solid transparent", borderRadius: "var(--r-sm)",
    fontFamily: "var(--font-sans)", fontWeight: 500,
    cursor: disabled ? "not-allowed" : "pointer",
    transition: "background var(--dur-fast) var(--ease), border-color var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease)",
    whiteSpace: "nowrap", width: full ? "100%" : "auto",
    opacity: disabled ? 0.5 : 1,
    letterSpacing: "-0.005em",
  };
  const sizes = {
    sm: { padding: "4px 8px", fontSize: 12, height: 26 },
    md: { padding: "6px 12px", fontSize: 13, height: 32 },
    lg: { padding: "10px 16px", fontSize: 14, height: 40 },
  };
  const kinds = {
    primary:   { background: danger ? "var(--danger)" : "var(--accent)", color: "var(--accent-ink)", borderColor: "transparent" },
    secondary: { background: "var(--surface)", color: "var(--ink)", borderColor: "var(--line)" },
    ghost:     { background: "transparent", color: "var(--ink-2)", borderColor: "transparent" },
    soft:      { background: "var(--surface-2)", color: "var(--ink)", borderColor: "transparent" },
    link:      { background: "transparent", color: "var(--accent)", borderColor: "transparent", padding: 0, height: "auto" },
  };
  return (
    <button onClick={onClick} disabled={disabled} style={{ ...base, ...sizes[size], ...kinds[kind], ...style }}
            onMouseEnter={e => { if (kind === "ghost") e.currentTarget.style.background = "var(--surface-2)"; }}
            onMouseLeave={e => { if (kind === "ghost") e.currentTarget.style.background = "transparent"; }}
            title={title}>
      {icon}{children}
    </button>
  );
};

// ---------- Chips ----------
const Chip = ({ children, tone = "neutral", icon, onClick, active, style }) => {
  const tones = {
    neutral: { color: "var(--ink-2)", bg: "var(--surface-2)", border: "transparent" },
    warm:    { color: "var(--reply-yes)", bg: "var(--accent-soft)", border: "var(--accent-line)" },
    cool:    { color: "var(--reply-fyi)", bg: "color-mix(in oklab, var(--reply-fyi) 10%, transparent)", border: "transparent" },
    muted:   { color: "var(--reply-news)", bg: "var(--surface-2)", border: "transparent" },
    success: { color: "var(--success)", bg: "var(--success-soft)", border: "transparent" },
    warning: { color: "var(--warning)", bg: "var(--warning-soft)", border: "transparent" },
    outline: { color: "var(--ink-2)", bg: "transparent", border: "var(--line)" },
  };
  const t = tones[tone] || tones.neutral;
  return (
    <span onClick={onClick} style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      padding: "2px 7px", borderRadius: 999,
      fontSize: 11, fontWeight: 500, lineHeight: 1.4,
      fontFamily: "var(--font-mono)", letterSpacing: "0.01em",
      color: t.color, background: t.bg, border: `1px solid ${t.border}`,
      cursor: onClick ? "pointer" : "default",
      ...(active ? { boxShadow: "inset 0 0 0 1px var(--accent)" } : {}),
      ...style
    }}>
      {icon}{children}
    </span>
  );
};

// ---------- Card ----------
const Card = ({ children, style, padding = 14, hoverable, onClick }) => (
  <div onClick={onClick} style={{
    background: "var(--surface)",
    border: "1px solid var(--line)",
    borderRadius: "var(--r-md)",
    padding,
    cursor: onClick ? "pointer" : "default",
    transition: "border-color var(--dur-fast) var(--ease), background var(--dur-fast) var(--ease)",
    ...style,
  }}
  onMouseEnter={e => { if (hoverable) e.currentTarget.style.borderColor = "var(--line-strong)"; }}
  onMouseLeave={e => { if (hoverable) e.currentTarget.style.borderColor = "var(--line)"; }}>
    {children}
  </div>
);

// ---------- Section header ----------
const SectionLabel = ({ children, right, style }) => (
  <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between",
                margin: "0 0 8px", padding: "0 2px", ...style }}>
    <div className="mono" style={{
      fontSize: 10, letterSpacing: "0.08em", textTransform: "uppercase",
      color: "var(--ink-4)", fontWeight: 500,
    }}>{children}</div>
    {right && <div style={{ fontSize: 11, color: "var(--ink-4)" }}>{right}</div>}
  </div>
);

// ---------- Tier indicator ----------
const TierPill = ({ tier = 2, total = 3, label = "Learning" }) => (
  <div style={{
    display: "inline-flex", alignItems: "center", gap: 6,
    padding: "3px 8px 3px 6px", borderRadius: 999,
    background: "var(--surface-2)", border: "1px solid var(--line)",
    fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--ink-2)",
  }}>
    <span style={{ display: "inline-flex", gap: 2 }}>
      {Array.from({ length: total }).map((_, i) => (
        <span key={i} style={{
          width: 5, height: 5, borderRadius: 999,
          background: i < tier ? "var(--accent)" : "var(--line-strong)",
          transition: "background var(--dur) var(--ease)",
        }} />
      ))}
    </span>
    <span>tier {tier}/{total}</span>
    <span style={{ color: "var(--ink-4)" }}>· {label}</span>
  </div>
);

// ---------- Reply-or-not chip (the 4 states) ----------
const ReplyChip = ({ state, size = "sm" }) => {
  const map = {
    yes:    { tone: "warm",  label: "reply" },
    optional: { tone: "neutral", label: "optional" },
    fyi:    { tone: "cool", label: "fyi" },
    news:   { tone: "muted", label: "newsletter" },
  };
  const m = map[state] || map.fyi;
  return <Chip tone={m.tone}>{m.label}</Chip>;
};

// ---------- Avatar (initials) ----------
const Avatar = ({ name = "?", size = 28, hue = 0 }) => {
  const initials = name.split(" ").map(s => s[0]).filter(Boolean).slice(0,2).join("").toUpperCase();
  const bg = `oklch(0.82 0.05 ${hue})`;
  const fg = `oklch(0.30 0.06 ${hue})`;
  return (
    <div style={{
      width: size, height: size, borderRadius: 999,
      background: bg, color: fg, display: "inline-flex",
      alignItems: "center", justifyContent: "center",
      fontSize: Math.round(size * 0.36), fontWeight: 500,
      fontFamily: "var(--font-sans)", flexShrink: 0,
    }}>{initials}</div>
  );
};

// stable hue per name
const hueFor = (name) => {
  let h = 0;
  for (let i = 0; i < (name||"").length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return h;
};

// ---------- Toast ----------
const Toast = ({ message, action, onAction, onDismiss, visible }) => {
  if (!visible) return null;
  return (
    <div style={{
      position: "absolute", left: 12, right: 12, bottom: 84,
      background: "var(--ink)", color: "var(--bg)",
      borderRadius: "var(--r-md)", padding: "10px 12px",
      display: "flex", alignItems: "center", justifyContent: "space-between",
      gap: 12, fontSize: 13, boxShadow: "var(--shadow-md)",
      animation: "slideUp var(--dur) var(--ease)", zIndex: 30,
    }}>
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{message}</span>
      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
        {action && <button onClick={onAction} style={{
          background: "transparent", border: "none", color: "var(--accent)",
          padding: "0 4px", fontSize: 13, fontWeight: 500, cursor: "pointer",
        }}>{action}</button>}
        <button onClick={onDismiss} style={{
          background: "transparent", border: "none", color: "var(--ink-4)",
          padding: 4, display: "flex", cursor: "pointer",
        }}>{Icons.x}</button>
      </div>
    </div>
  );
};

// ---------- Loading dots ----------
const Dots = () => (
  <span style={{ display: "inline-flex", gap: 3, alignItems: "center" }}>
    {[0,1,2].map(i => <span key={i} style={{
      width: 4, height: 4, borderRadius: 999, background: "currentColor",
      animation: `pulse 1.2s ${i*0.18}s infinite var(--ease)`,
    }} />)}
  </span>
);

Object.assign(window, {
  Icons, Icon, Button, Chip, Card, SectionLabel,
  TierPill, ReplyChip, Avatar, hueFor, Toast, Dots,
});
