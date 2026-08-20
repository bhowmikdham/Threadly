// Webmail — neutral mail client, layout patterned after a modern inbox
// (tabbed primary view, sidebar with counts, tracking banner, row hover actions).
// Original chrome — no branded marks copied.

const { useState: useState_wm, useEffect: useEffect_wm } = React;

function Webmail({ data, view, onOpen, onBack, onCompose, theme }) {
  return (
    <div style={{
      flex: 1, height: "100%",
      background: "var(--bg)",
      display: "flex", flexDirection: "column",
      overflow: "hidden",
      color: "var(--ink)",
    }}>
      <WebmailHeader />
      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        <WebmailSidebar onCompose={onCompose} />
        <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column", background: "var(--bg-elev)" }}>
          {view === "inbox" && <InboxList data={data} onOpen={onOpen} />}
          {view === "thread" && <ThreadView data={data} onBack={onBack} />}
          {view === "compose" && <ComposeView data={data} onBack={onBack} />}
        </div>
      </div>
    </div>
  );
}

function WebmailHeader() {
  return (
    <div style={{
      height: 56, flexShrink: 0,
      display: "flex", alignItems: "center",
      padding: "0 16px", gap: 16,
      background: "var(--bg)",
      borderBottom: "1px solid var(--line)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, width: 200, flexShrink: 0 }}>
        <div style={{
          width: 32, height: 32, borderRadius: 8,
          background: "var(--ink)", color: "var(--bg)",
          display: "grid", placeItems: "center",
        }}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="5" width="18" height="14" rx="2" />
            <path d="m3 7 9 6 9-6" />
          </svg>
        </div>
        <span className="display" style={{ fontSize: 17, fontWeight: 500, letterSpacing: "-0.015em" }}>Mailbox</span>
      </div>
      <div style={{ flex: 1, maxWidth: 720, position: "relative" }}>
        <div style={{ position: "absolute", left: 12, top: 11, color: "var(--ink-4)" }}>{Icons.search}</div>
        <input placeholder="Search mail" style={{
          width: "100%", height: 38, borderRadius: 8,
          background: "var(--surface-2)", border: "1px solid transparent",
          padding: "0 12px 0 38px", fontSize: 13.5, color: "var(--ink)",
          fontFamily: "var(--font-sans)", outline: "none",
        }} />
      </div>
      <div style={{ flex: 1 }} />
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <button style={{
          width: 32, height: 32, borderRadius: 999, border: "none",
          background: "transparent", color: "var(--ink-3)",
          cursor: "pointer", display: "grid", placeItems: "center",
        }} title="Settings">{Icons.settings}</button>
        <Avatar name="Maya Chen" size={30} hue={210} />
      </div>
    </div>
  );
}

function WebmailSidebar({ onCompose }) {
  const items = [
    { icon: Icons.inbox,   label: "Inbox",     count: "23",  active: true, bold: true },
    { icon: Icons.star,    label: "Starred",   count: null },
    { icon: Icons.clock,   label: "Snoozed",   count: "3" },
    { icon: Icons.warning, label: "Important", count: null },
    { icon: Icons.send,    label: "Sent",      count: null },
    { icon: Icons.doc,     label: "Drafts",    count: "4" },
    { icon: Icons.archive, label: "Archive",   count: null },
    { icon: Icons.trash,   label: "Trash",     count: null },
  ];
  // MailMind-applied auto-labels
  const labels = [
    { color: "var(--reply-yes)", label: "Action", count: "4" },
    { color: "var(--warning)",   label: "Ask",    count: "2" },
    { color: "var(--reply-fyi)", label: "FYI",    count: "6" },
    { color: "var(--reply-news)", label: "Social", count: "11" },
  ];
  return (
    <div style={{
      width: 220, flexShrink: 0,
      padding: "8px 8px 14px",
      overflowY: "auto",
      background: "var(--bg)",
      display: "flex", flexDirection: "column", gap: 4,
    }} className="thin-scroll">
      <button onClick={onCompose} style={{
        display: "inline-flex", alignItems: "center", gap: 10,
        height: 50, borderRadius: 16,
        background: "var(--accent-soft)", color: "var(--accent)",
        border: "1px solid var(--accent-line)",
        padding: "0 22px 0 18px", fontSize: 14.5, fontWeight: 500,
        cursor: "pointer", fontFamily: "var(--font-sans)",
        marginBottom: 8, alignSelf: "flex-start",
        boxShadow: "var(--shadow-sm)",
      }}>
        {Icons.edit}
        Compose
      </button>

      <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
        {items.map((it, i) => (
          <SidebarItem key={i} {...it} />
        ))}
        <SidebarItem icon={Icons.chevronDown} label="More" muted />
      </div>

      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "16px 14px 6px",
      }}>
        <span className="mono" style={{
          fontSize: 10.5, color: "var(--ink-4)",
          letterSpacing: "0.06em", textTransform: "uppercase", fontWeight: 500,
        }}>Labels · auto</span>
        <button style={{
          width: 18, height: 18, borderRadius: 4,
          background: "transparent", border: "none", color: "var(--ink-3)",
          display: "grid", placeItems: "center", cursor: "pointer",
        }}>{Icons.plus}</button>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
        {labels.map((l, i) => (
          <SidebarItem key={i} icon={<svg width="16" height="16" viewBox="0 0 24 24" fill={l.color} stroke="none"><path d="M4 4h11l5 8-5 8H4z" /></svg>} label={l.label} count={l.count} />
        ))}
      </div>
    </div>
  );
}

function SidebarItem({ icon, label, count, active, bold, muted }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 14,
      padding: "7px 14px", height: 30, borderRadius: 999,
      background: active ? "var(--accent-soft)" : "transparent",
      color: active ? "var(--accent)" : muted ? "var(--ink-4)" : "var(--ink-2)",
      cursor: "pointer", fontSize: 13,
      fontWeight: bold || active ? 500 : 400,
      transition: "background var(--dur-fast) var(--ease)",
    }}
    onMouseEnter={e => { if (!active) e.currentTarget.style.background = "var(--surface-2)"; }}
    onMouseLeave={e => { if (!active) e.currentTarget.style.background = "transparent"; }}
    >
      <span style={{ display: "flex" }}>{icon}</span>
      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
      {count != null && (
        <span className="mono tnum" style={{
          fontSize: 11, fontWeight: 500,
          color: active ? "var(--accent)" : "var(--ink-3)",
        }}>{count}</span>
      )}
    </div>
  );
}

// ---------- Tabs ----------
function InboxTabs({ tab, setTab }) {
  const tabs = [
    { id: "primary", icon: Icons.inbox,   label: "Primary",   active: true },
    { id: "action",  dot: "var(--reply-yes)",  label: "Action",  count: "4 new" },
    { id: "ask",     dot: "var(--warning)",    label: "Ask",     count: "2 new" },
    { id: "fyi",     dot: "var(--reply-fyi)",  label: "FYI",     count: "6 new" },
    { id: "social",  dot: "var(--reply-news)", label: "Social",  count: "11 new" },
  ];
  return (
    <div style={{
      display: "flex", padding: "0 12px",
      background: "var(--bg-elev)",
      borderBottom: "1px solid var(--line)",
    }}>
      {tabs.map(t => (
        <button key={t.id} onClick={() => setTab(t.id)} style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "12px 18px",
          background: "transparent", border: "none",
          borderBottom: tab === t.id ? "3px solid var(--accent)" : "3px solid transparent",
          color: tab === t.id ? "var(--ink)" : "var(--ink-3)",
          fontSize: 13.5, fontWeight: tab === t.id ? 500 : 400,
          cursor: "pointer", fontFamily: "var(--font-sans)",
          minWidth: 160, maxWidth: 240, textAlign: "left",
          marginBottom: -1, position: "relative",
        }}>
          {t.icon ? (
            <span style={{ color: tab === t.id ? "var(--accent)" : "var(--ink-4)" }}>{t.icon}</span>
          ) : (
            <span style={{ width: 16, height: 16, borderRadius: 999, background: t.dot, flexShrink: 0 }} />
          )}
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.label}</span>
          {t.count && (
            <span style={{
              padding: "1px 7px", borderRadius: 999,
              background: tab === t.id ? "var(--accent)" : t.dot,
              color: "var(--accent-ink)",
              fontSize: 10.5, fontFamily: "var(--font-mono)", fontWeight: 500,
              flexShrink: 0,
            }}>{t.count}</span>
          )}
        </button>
      ))}
    </div>
  );
}

// ---------- Inbox list ----------
function InboxList({ data, onOpen }) {
  const [tab, setTab] = useState_wm("primary");
  const filtered = tab === "primary" ? data.inbox :
    data.inbox.filter(r => r.label.toLowerCase() === tab);

  return (
    <div style={{
      flex: 1, display: "flex", flexDirection: "column",
      overflow: "hidden", padding: 12,
    }}>
      <div style={{
        flex: 1, background: "var(--surface)",
        border: "1px solid var(--line)",
        borderRadius: 12, overflow: "hidden",
        display: "flex", flexDirection: "column",
        boxShadow: "var(--shadow-sm)",
      }}>
        {/* Top toolbar */}
        <div style={{
          height: 48, flexShrink: 0,
          display: "flex", alignItems: "center",
          padding: "0 12px 0 16px", gap: 4,
        }}>
          <input type="checkbox" style={{ accentColor: "var(--accent)" }} />
          <button style={toolbarBtn}>{Icons.chevronDown}</button>
          <button style={toolbarBtn} title="Refresh">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12a9 9 0 0 1-15.36 6.36" />
              <path d="M3 12a9 9 0 0 1 15.36-6.36" />
              <path d="M21 4v5h-5" />
              <path d="M3 20v-5h5" />
            </svg>
          </button>
          <button style={toolbarBtn}>{Icons.more}</button>
          <div style={{ flex: 1 }} />
          <span className="mono tnum" style={{ fontSize: 11.5, color: "var(--ink-3)", marginRight: 8 }}>1–10 of 412</span>
          <button style={toolbarBtn}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="m15 6-6 6 6 6" /></svg>
          </button>
          <button style={toolbarBtn}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="m9 6 6 6-6 6" /></svg>
          </button>
        </div>

        <InboxTabs tab={tab} setTab={setTab} />

        {/* MailMind tracking-style banner */}
        <TrackingBanner />

        {/* Rows */}
        <div className="thin-scroll" style={{ overflowY: "auto", flex: 1 }}>
          {filtered.map((row) => (
            <InboxRow key={row.id} row={row} onClick={() => onOpen && onOpen(row.id)} />
          ))}
        </div>
      </div>
    </div>
  );
}

const toolbarBtn = {
  width: 32, height: 32, borderRadius: 999, border: "none",
  background: "transparent", color: "var(--ink-3)",
  cursor: "pointer", display: "grid", placeItems: "center",
};

// ---------- Tracking banner ("Happening soon") ----------
function TrackingBanner() {
  return (
    <div style={{
      borderBottom: "1px solid var(--line)",
      background: "var(--bg-elev)",
    }}>
      <div style={{
        padding: "8px 16px",
        display: "flex", alignItems: "center", gap: 10,
        fontSize: 12, color: "var(--ink-3)",
        borderBottom: "1px solid var(--line)",
      }}>
        <span style={{ flex: 1 }}>Happening soon</span>
        <button style={{ background: "transparent", border: "none", color: "var(--ink-4)", padding: 2, cursor: "pointer", display: "flex" }}>{Icons.x}</button>
      </div>
      <div style={{
        padding: "12px 16px",
        display: "flex", alignItems: "center", gap: 14,
      }}>
        <div style={{
          width: 36, height: 36, borderRadius: 8,
          background: "var(--surface-2)",
          display: "grid", placeItems: "center",
          flexShrink: 0,
        }}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ color: "var(--ink-3)" }}>
            <rect x="3" y="7" width="18" height="13" rx="2" />
            <path d="M3 11h18M8 7V4h8v3" />
          </svg>
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13.5, color: "var(--ink)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            Northgrid kickoff offsite — flights & hotel itinerary
          </div>
          <div className="mono" style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 2 }}>
            arrives in 2 days · SFO → AUS · Wed May 15
          </div>
        </div>
        <Button kind="primary" size="sm">View itinerary</Button>
        <button style={toolbarBtn}>{Icons.more}</button>
      </div>
    </div>
  );
}

// ---------- Inbox row ----------
function InboxRow({ row, onClick }) {
  const [hover, setHover] = useState_wm(false);
  const labelColor = {
    Action: "var(--reply-yes)", Ask: "var(--warning)",
    FYI: "var(--reply-fyi)", Social: "var(--reply-news)",
  }[row.label];
  return (
    <div onClick={onClick}
         onMouseEnter={() => setHover(true)}
         onMouseLeave={() => setHover(false)}
         style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "0 16px",
      height: 40,
      borderBottom: "1px solid var(--line)",
      cursor: "pointer", position: "relative",
      background: row.unread ? "var(--surface)" : "transparent",
      transition: "background var(--dur-fast) var(--ease)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 4, width: 80, flexShrink: 0 }}>
        <input type="checkbox" style={{ accentColor: "var(--accent)" }} onClick={e => e.stopPropagation()} />
        <button onClick={e => e.stopPropagation()} style={{ background: "transparent", border: "none", color: row.starred ? "var(--warning)" : "var(--ink-4)", cursor: "pointer", padding: 2, display: "flex" }}>{Icons.star}</button>
        {/* Importance marker */}
        <button onClick={e => e.stopPropagation()} style={{ background: "transparent", border: "none", color: row.reply === "yes" ? "var(--reply-yes)" : "var(--ink-4)", cursor: "pointer", padding: 2, display: "flex" }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill={row.reply === "yes" ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M4 7l8 5 8-5v10a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7z"/><path d="M4 7l8 5 8-5"/></svg>
        </button>
      </div>

      {/* Sender */}
      <div style={{ width: 168, flexShrink: 0, display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ fontSize: 13.5, fontWeight: row.unread ? 500 : 400, color: row.unread ? "var(--ink)" : "var(--ink-2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {row.from}
        </span>
        {/* "New" badge for unread */}
        {row.unread && (
          <span style={{
            padding: "0 6px", height: 16, borderRadius: 999,
            background: "var(--accent-soft)", color: "var(--accent)",
            fontSize: 9.5, fontFamily: "var(--font-mono)", fontWeight: 500,
            display: "inline-flex", alignItems: "center", letterSpacing: "0.05em",
            border: "1px solid var(--accent-line)",
            textTransform: "uppercase", flexShrink: 0,
          }}>new</span>
        )}
      </div>

      {/* Subject + preview */}
      <div style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 8, overflow: "hidden" }}>
        {/* MailMind overlay: reply-or-not + commitment */}
        <ReplyChip state={row.reply} />
        {row.commitment && (
          <span title="Open commitment in this thread" style={{ color: "var(--warning)", display: "inline-flex", flexShrink: 0 }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 14 4 9l5-5M4 9h10a6 6 0 0 1 6 6v0a6 6 0 0 1-6 6h-3" />
            </svg>
          </span>
        )}
        <span style={{
          fontSize: 13.5, fontWeight: row.unread ? 500 : 400,
          color: row.unread ? "var(--ink)" : "var(--ink-2)",
          flexShrink: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>{row.subject}</span>
        <span style={{ fontSize: 13.5, color: "var(--ink-4)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>— {row.preview}</span>
      </div>

      {/* Right side: hover actions OR auto-label + attach + time */}
      {hover ? (
        <div style={{ display: "flex", gap: 2, flexShrink: 0 }}>
          <button onClick={e => e.stopPropagation()} style={toolbarBtn} title="Archive">{Icons.archive}</button>
          <button onClick={e => e.stopPropagation()} style={toolbarBtn} title="Delete">{Icons.trash}</button>
          <button onClick={e => e.stopPropagation()} style={toolbarBtn} title="Mark unread">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/><circle cx="20" cy="6" r="3" fill="var(--accent)" stroke="none"/></svg>
          </button>
          <button onClick={e => e.stopPropagation()} style={toolbarBtn} title="Snooze">{Icons.clock}</button>
        </div>
      ) : (
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          {row.attach && <span style={{ color: "var(--ink-4)" }}>{Icons.paperclip}</span>}
          <span style={{
            padding: "1px 7px", borderRadius: 3, fontSize: 10.5,
            background: `color-mix(in oklab, ${labelColor} 14%, transparent)`,
            color: labelColor, fontFamily: "var(--font-mono)",
            border: `1px solid color-mix(in oklab, ${labelColor} 25%, transparent)`,
          }}>{row.label}</span>
          <span className="mono tnum" style={{ fontSize: 11, color: "var(--ink-4)", width: 44, textAlign: "right" }}>
            {row.time}
          </span>
        </div>
      )}
    </div>
  );
}

// ---------- Thread view ----------
function ThreadView({ data, onBack }) {
  const t = data.openThread;
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", padding: 12, overflow: "hidden" }}>
      <div style={{
        flex: 1, background: "var(--surface)",
        border: "1px solid var(--line)",
        borderRadius: 12, overflow: "hidden",
        display: "flex", flexDirection: "column",
        boxShadow: "var(--shadow-sm)",
      }}>
        <div style={{
          height: 48, flexShrink: 0,
          borderBottom: "1px solid var(--line)",
          display: "flex", alignItems: "center",
          padding: "0 12px 0 8px", gap: 4,
        }}>
          <button style={toolbarBtn} onClick={onBack} title="Back">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="m15 6-6 6 6 6"/></svg>
          </button>
          <span style={{ width: 1, height: 18, background: "var(--line)", margin: "0 4px" }} />
          <button style={toolbarBtn} title="Archive">{Icons.archive}</button>
          <button style={toolbarBtn} title="Delete">{Icons.trash}</button>
          <button style={toolbarBtn} title="Mark unread">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></svg>
          </button>
          <button style={toolbarBtn} title="Snooze">{Icons.clock}</button>
          <button style={toolbarBtn} title="Label">{Icons.label}</button>
          <span style={{ width: 1, height: 18, background: "var(--line)", margin: "0 4px" }} />
          {/* MailMind inline button */}
          <button style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "5px 10px", borderRadius: 6,
            background: "var(--accent-soft)", border: "1px solid var(--accent-line)",
            color: "var(--accent)", fontSize: 12.5, fontWeight: 500, cursor: "pointer",
            fontFamily: "var(--font-sans)",
          }}>
            {Icons.sparkle}
            <span>Summarize with MailMind</span>
          </button>
          <div style={{ flex: 1 }} />
          <span className="mono tnum" style={{ fontSize: 11.5, color: "var(--ink-3)", marginRight: 4 }}>1 of 412</span>
          <button style={toolbarBtn}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="m15 6-6 6 6 6"/></svg>
          </button>
          <button style={toolbarBtn}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="m9 6 6 6-6 6"/></svg>
          </button>
        </div>

        <div className="thin-scroll" style={{ flex: 1, overflowY: "auto", padding: "24px 32px 32px" }}>
          <div style={{ display: "flex", alignItems: "flex-start", gap: 14, marginBottom: 18 }}>
            <div className="display" style={{
              flex: 1, fontSize: 24, lineHeight: 1.2, fontWeight: 500,
              letterSpacing: "-0.018em", color: "var(--ink)",
            }}>{t.subject}</div>
            <span style={{
              padding: "2px 8px", borderRadius: 3, fontSize: 10.5,
              background: `color-mix(in oklab, var(--reply-yes) 14%, transparent)`,
              color: "var(--reply-yes)", fontFamily: "var(--font-mono)",
              border: `1px solid color-mix(in oklab, var(--reply-yes) 25%, transparent)`,
              flexShrink: 0, marginTop: 4,
            }}>Action</span>
          </div>
          {t.messages.map((m, i) => (
            <Message key={i} m={m} expanded={i === t.messages.length - 1} />
          ))}

          <div style={{ marginTop: 16, display: "flex", gap: 8, paddingLeft: 48 }}>
            <Button kind="secondary" icon={Icons.reply}>Reply</Button>
            <Button kind="ghost" icon={Icons.send}>Forward</Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Message({ m, expanded }) {
  return (
    <div style={{
      borderTop: "1px solid var(--line)",
      padding: "18px 0",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: expanded ? 14 : 0 }}>
        <Avatar name={m.from} size={36} hue={hueFor(m.from)} />
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12 }}>
            <div>
              <span style={{ fontSize: 14, fontWeight: 500 }}>{m.from}</span>
              <span style={{ fontSize: 12, color: "var(--ink-4)", marginLeft: 8 }}>to me</span>
            </div>
            <span className="mono" style={{ fontSize: 11, color: "var(--ink-4)" }}>{m.time}</span>
          </div>
          {!expanded && (
            <div style={{ fontSize: 13, color: "var(--ink-3)", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {m.body}
            </div>
          )}
        </div>
      </div>
      {expanded && (
        <div style={{ paddingLeft: 48, fontSize: 14.5, color: "var(--ink)", lineHeight: 1.65 }}>
          {m.body}
        </div>
      )}
    </div>
  );
}

function ComposeView({ data, onBack }) {
  return (
    <div style={{ flex: 1, padding: 12 }}>
      <div style={{ height: 44, borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", padding: "0 16px" }}>
        <Button kind="ghost" size="sm" icon={Icons.back} onClick={onBack}>Back</Button>
      </div>
      <div style={{ padding: 24 }}>
        <div className="display" style={{ fontSize: 20, fontWeight: 500 }}>Compose</div>
      </div>
    </div>
  );
}

Object.assign(window, { Webmail });
