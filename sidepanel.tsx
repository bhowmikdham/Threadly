import { useEffect, useRef, useState } from "react"

import "./style.css"

import { ConnectorList, type ConnectorId } from "./components/Connectors"
import { ContextPicker } from "./components/ContextPicker"
import { GmailIcon, Icon, Logo } from "./components/Icon"
import { Settings } from "./components/Settings"
import { TaskCard } from "./components/TaskCard"
import { VoiceOrb } from "./components/VoiceOrb"
import { api, bridge, errorText } from "./lib/api"
import { requestBackendAccess } from "./lib/backend-access"
import { gmailUrlShowsEmail } from "./lib/gmail-context"
import {
  calendarReadReady,
  schedulingReadiness,
  type PreferencesState
} from "./lib/scheduling-readiness"
import { spokenReply } from "./lib/spoken-reply"
import type { Capability, User } from "./lib/types"
import { useAssistant } from "./lib/use-assistant"

export default function SidePanel() {
  const [user, setUser] = useState<User | null>(null),
    [serverOrigin, setServerOrigin] = useState(""),
    [capabilities, setCapabilities] = useState<Capability[]>([]),
    [ready, setReady] = useState(false),
    [busy, setBusy] = useState(false),
    [signingOut, setSigningOut] = useState(false),
    [error, setError] = useState(""),
    [settings, setSettings] = useState(false),
    [connector, setConnector] = useState<ConnectorId | null>(null),
    [dark, setDark] = useState(false),
    [preferencesState, setPreferencesState] =
      useState<PreferencesState>("loading")
  const authEpoch = useRef(0)
  const refresh = async () => {
    const epoch = ++authEpoch.current
    const s = await bridge<any>({ type: "STATUS" })
    if (epoch !== authEpoch.current) return
    setServerOrigin(s.origin)
    setUser(s.user)
    if (s.user) {
      try {
        const c = await api("/assistant/capabilities")
        if (epoch !== authEpoch.current) return
        setCapabilities(c.capabilities)
        if (!calendarReadReady(c.capabilities)) {
          setPreferencesState("missing")
          return
        }
        setPreferencesState("loading")
        try {
          await api("/calendar/preferences")
          if (epoch === authEpoch.current) setPreferencesState("ready")
        } catch (e) {
          if (epoch === authEpoch.current)
            setPreferencesState(
              e?.code === "calendar_preferences_missing"
                ? "missing"
                : "unavailable"
            )
        }
      } catch (e) {
        if (epoch !== authEpoch.current) return
        if (e.status === 401) setUser(null)
        throw e
      }
    } else {
      setCapabilities([])
      setPreferencesState("missing")
    }
  }
  useEffect(() => {
    void refresh()
      .catch((e) => setError(errorText(e)))
      .finally(() => setReady(true))
    void chrome.storage.local
      .get("darkMode")
      .then((s) => setDark(s.darkMode === true))
    const changed = (changes: any, area: string) => {
      if (
        area === "session" &&
        changes.threadlySession &&
        !changes.threadlySession.newValue
      ) {
        authEpoch.current++
        setUser(null)
        setCapabilities([])
        setPreferencesState("missing")
      }
    }
    chrome.storage.onChanged.addListener(changed)
    return () => chrome.storage.onChanged.removeListener(changed)
  }, [])
  const login = async () => {
    setBusy(true)
    setError("")
    try {
      await requestBackendAccess(serverOrigin)
      await bridge({ type: "LOGIN" })
      await refresh()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  const logout = async () => {
    authEpoch.current++
    setSigningOut(true)
    setError("")
    try {
      const result = await bridge<{ serverRevoked: boolean }>({
        type: "LOGOUT"
      })
      if (!result.serverRevoked)
        setError(
          "Signed out on this device, but Threadly could not confirm server sign-out. Other sessions may remain active. Sign in and sign out again when the server is reachable."
        )
    } catch {
      // The worker may fail before replying. Do not leave its local token behind.
      await chrome.storage.session.remove("threadlySession")
      setError(
        "Signed out on this device, but Threadly could not confirm server sign-out. Other sessions may remain active. Sign in and sign out again when the server is reachable."
      )
    } finally {
      setUser(null)
      setCapabilities([])
      setPreferencesState("missing")
      setSettings(false)
      setSigningOut(false)
    }
  }
  const theme = () => {
    setDark(!dark)
    void chrome.storage.local.set({ darkMode: !dark })
  }
  return (
    <main className={dark ? "threadly dark" : "threadly"}>
      {/* Sign-in screen only: Chrome's panel header shows the name, and the
          Settings page has its own heading and Back to chat. */}
      {!user && !settings && (
        <header className="app-header">
          <span className="wordmark" />
          <button
            className="icon-button"
            aria-label="Settings"
            title="Settings"
            onClick={() => setSettings(true)}>
            <Icon name="menu" />
          </button>
        </header>
      )}
      {!ready ? (
        <p className="empty" role="status">
          Connecting…
        </p>
      ) : (
        <>
          {settings && (
            <Settings
              key={user?.id ?? "guest"}
              user={user}
              initialConnector={connector}
              capabilities={capabilities}
              preferencesState={preferencesState}
              onAuth={refresh}
              onClose={() => setSettings(false)}
              onPreferences={(value) =>
                setPreferencesState(value ? "ready" : "missing")
              }
            />
          )}
          {user && (
            <div className="assistant-host" hidden={settings}>
              <Assistant
                key={user.id}
                user={user}
                capabilities={capabilities}
                calendarStatus={schedulingReadiness(
                  capabilities,
                  preferencesState
                )}
                openSettings={(id) => {
                  setConnector(id || null)
                  setSettings(true)
                }}
                onAuth={refresh}
                logout={logout}
                theme={theme}
                dark={dark}
              />
            </div>
          )}
          {!user && !settings && (
            <section className="welcome">
              <div>
                <span className="welcome-mark welcome-logo">
                  <Logo height={20} />
                </span>
                <h1>
                  A little less work.
                  <br />A little more flow.
                </h1>
                <p>Your email, your calendar, one conversation.</p>
                <button
                  className="primary login-button"
                  disabled={busy || signingOut}
                  onClick={login}>
                  {signingOut
                    ? "Signing out…"
                    : busy
                      ? "Connecting…"
                      : "Sign in with Google"}
                </button>
                <p className="muted">
                  Read what matters. Find the words. Make time.
                  <br />
                  You review before anything is sent or booked.
                </p>
              </div>
            </section>
          )}
        </>
      )}
      {error && (
        <p className="global-error" role="alert">
          {error}
          <button
            className="icon-button"
            aria-label="Dismiss error"
            onClick={() => setError("")}>
            <Icon name="close" size={14} />
          </button>
        </p>
      )}
    </main>
  )
}
function Assistant({
  user,
  capabilities,
  calendarStatus,
  openSettings,
  onAuth,
  logout,
  theme,
  dark
}: {
  user: User
  capabilities: Capability[]
  calendarStatus: ReturnType<typeof schedulingReadiness>
  openSettings: (connector?: ConnectorId) => void
  onAuth: () => Promise<void>
  logout: () => void
  theme: () => void
  dark: boolean
}) {
  const c = useAssistant(user),
    [message, setMessage] = useState(""),
    [menu, setMenu] = useState(false),
    [account, setAccount] = useState(false),
    [sources, setSources] = useState(false),
    [contextOpen, setContextOpen] = useState(false),
    [history, setHistory] = useState<any>(null),
    [subjects, setSubjects] = useState<Record<string, string>>({}),
    [historyCursor, setHistoryCursor] = useState<string | null>(null),
    [recording, setRecording] = useState(false),
    [openEmail, setOpenEmail] = useState(false),
    [approvalInfo, setApprovalInfo] = useState(false),
    [connecting, setConnecting] = useState(""),
    [connectError, setConnectError] = useState("")
  const last = useRef<HTMLDivElement>(null),
    input = useRef<HTMLTextAreaElement>(null),
    scroll = useRef<HTMLDivElement>(null),
    atBottom = useRef(true),
    positionedSearch = useRef<string | null>(null)
  const startupBusy = c.busy || c.restoring
  const inputBusy = startupBusy || c.restoreFailed
  useEffect(() => {
    void c.selectActive(true)
  }, [])
  // "Ask about the open email" only makes sense while Gmail is showing an email.
  useEffect(() => {
    const check = () =>
      void chrome.tabs
        ?.query({ active: true, lastFocusedWindow: true })
        .then((tabs) => setOpenEmail(gmailUrlShowsEmail(tabs[0]?.url)))
        .catch(() => setOpenEmail(false))
    const changed = (_id: number, info: { url?: string }) => {
      if (info.url) check()
    }
    check()
    chrome.tabs?.onActivated?.addListener(check)
    chrome.tabs?.onUpdated?.addListener(changed)
    chrome.windows?.onFocusChanged?.addListener(check)
    return () => {
      chrome.tabs?.onActivated?.removeListener(check)
      chrome.tabs?.onUpdated?.removeListener(changed)
      chrome.windows?.onFocusChanged?.removeListener(check)
    }
  }, [])
  useEffect(() => {
    const newest = c.entries.at(-1)
    if (newest?.inbox && positionedSearch.current !== newest.id) {
      positionedSearch.current = newest.id
      atBottom.current = false
      scroll.current
        ?.querySelector(`[data-entry-id="${newest.id}"] .assistant-message`)
        ?.scrollIntoView({ block: "start" })
    } else if (atBottom.current && !newest?.inbox)
      last.current?.scrollIntoView({ block: "end" })
  }, [c.entries])
  useEffect(() => {
    if (input.current) {
      input.current.style.height = "auto"
      input.current.style.height = `${Math.min(input.current.scrollHeight, 150)}px`
    }
  }, [message])
  useEffect(() => {
    const ids = [
      ...new Set(
        (history || [])
          .map(
            (t: any) => t.effective_context_snapshot_id || t.context_snapshot_id
          )
          .filter(Boolean)
      )
    ].filter((id: string) => !(id in subjects)) as string[]
    for (const id of ids)
      void api(`/assistant/context-snapshots/${encodeURIComponent(id)}`)
        .then((snap) => api(`/threads/${encodeURIComponent(snap.thread_id)}`))
        .then((t) =>
          setSubjects((s) => ({ ...s, [id]: t.thread.subject || "" }))
        )
        .catch(() => setSubjects((s) => ({ ...s, [id]: "" })))
  }, [history])
  const historyPage = async (more = false) => {
    try {
      const r = await api(
        "/assistant/tasks?page_size=20" +
          (more && historyCursor
            ? `&cursor=${encodeURIComponent(historyCursor)}`
            : "")
      )
      setHistory((old) => (more ? [...(old || []), ...r.tasks] : r.tasks))
      setHistoryCursor(r.next_cursor)
      setMenu(false)
    } catch (e) {
      c.setError(errorText(e))
    }
  }
  const send = (e?: React.FormEvent) => {
    e?.preventDefault()
    if (inputBusy || !message.trim()) return
    const text = message
    setMessage("")
    setSources(false)
    atBottom.current = true
    void c.submit(text)
  }
  const suggest = (text: string) => {
    if (inputBusy) return
    atBottom.current = true
    void c.submit(text)
  }
  // The mic opens voice mode: a spoken back-and-forth. Each thing the user
  // says is sent like a typed request, and Threadly's reply is read aloud.
  const dictate = () => setRecording(true)
  const entriesNow = useRef(c.entries)
  entriesNow.current = c.entries
  const busyNow = useRef(inputBusy)
  busyNow.current = inputBusy || c.busy
  const voiceRespond = async (said: string) => {
    if (busyNow.current)
      return "I'm still finishing the last request. Give me a moment."
    const before = entriesNow.current.length
    atBottom.current = true
    void c.submit(said)
    const deadline = Date.now() + 90_000
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 400))
      const entry = entriesNow.current
        .slice(before)
        .find((e) => e.instruction === said)
      const reply = spokenReply(entry)
      if (reply) return reply
    }
    return "That's taking a while. I'll keep working on it in the chat."
  }
  const voiceDone = (error?: string) => {
    setRecording(false)
    if (error) c.setError(error)
    input.current?.focus()
  }
  const newChat = async () => {
    await c.newChat()
    setHistory(null)
    setContextOpen(false)
    setMessage("")
    setMenu(false)
  }
  const title = c.entries.length
    ? /^(hi|hey|hello)[.!?]?$/i.test(c.entries[0].instruction.trim())
      ? "New conversation"
      : c.entries[0].instruction
    : "New conversation"
  // Connect straight from the list, with the same Google permission request
  // Settings uses; the rows update once the backend reports the new access.
  const connect = async (service: string) => {
    setConnecting(service)
    setConnectError("")
    try {
      await bridge({
        type: "LOGIN",
        capabilities: [service === "calendar" ? "calendar_read" : "gmail_read"]
      })
      await onAuth()
    } catch (e) {
      setConnectError(errorText(e))
    } finally {
      setConnecting("")
    }
  }
  // Recent chats: one row per email (labelled by its subject), leaving out
  // what is already open here. Requests without an email stay one per ask.
  const openTasks = new Set(c.entries.map((e) => e.task?.task_id))
  const contextOf = (t: any): string =>
    t.effective_context_snapshot_id || t.context_snapshot_id || ""
  const newestFirst = [...(history || [])].sort(
    (a: any, b: any) =>
      Date.parse(b.created_at || b.updated_at || "") -
        Date.parse(a.created_at || a.updated_at || "") || 0
  )
  const recent: { key: string; context: string; latest: any; count: number }[] =
    []
  for (const t of newestFirst) {
    if (openTasks.has(t.task_id)) continue
    const key =
      contextOf(t) || `ask:${t.instruction?.trim().toLowerCase() || t.task_id}`
    const group = recent.find((g) => g.key === key)
    if (group) group.count += 1
    else recent.push({ key, context: contextOf(t), latest: t, count: 1 })
  }
  const firstName = user.name?.trim().split(/\s+/)[0] || ""
  const displayName = user.name?.trim() || user.email.split("@")[0]
  const initials =
    (user.name || user.email)
      .trim()
      .split(/[\s@._-]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0].toUpperCase())
      .join("") || "T"
  const latest = c.entries.at(-1)
  return (
    <>
      <header className="chat-header">
        <button
          className="icon-button"
          aria-label="Conversation menu"
          title="Conversation menu"
          aria-expanded={menu}
          onClick={() => {
            setMenu(!menu)
            setAccount(false)
          }}>
          <Icon name="menu" />
        </button>
        <span className="chat-title" title={title}>
          {title}
        </span>
        <button
          className="icon-button"
          aria-label="New chat"
          title="New chat"
          disabled={startupBusy}
          onClick={() => void newChat()}>
          <Icon name="edit" />
        </button>
      </header>
      {/* Only shown when Calendar needs something; a ready Calendar stays quiet. */}
      {calendarStatus.state !== "ready" && (
        <div
          className={`calendar-status calendar-status-${calendarStatus.state}`}
          role="status">
          <Icon name="calendar" size={16} />
          <span>
            <b>{calendarStatus.label}</b>
            <small>{calendarStatus.detail}</small>
            {connectError && !menu && (
              <small className="connector-error" role="alert">
                {connectError}
              </small>
            )}
          </span>
          {calendarStatus.action && (
            <button
              disabled={!!connecting}
              onClick={() =>
                calendarStatus.state === "connect"
                  ? void connect("calendar")
                  : openSettings("calendar")
              }>
              {connecting === "calendar"
                ? "Connecting…"
                : calendarStatus.action}
            </button>
          )}
        </div>
      )}
      {menu && (
        <nav className="panel-menu" aria-label="Conversation menu">
          <div className="drawer-title">
            <span className="drawer-brand">
              <Logo height={15} />
              Threadly
            </span>
            <button
              className="icon-button"
              aria-label="Close conversation menu"
              onClick={() => setMenu(false)}>
              <Icon name="close" />
            </button>
          </div>
          <button
            className="drawer-new-chat"
            onClick={() => void newChat()}
            disabled={startupBusy}>
            <Icon name="edit" size={16} />
            New conversation
          </button>
          <p className="drawer-section">Conversations</p>
          <button
            className="drawer-row"
            disabled={inputBusy}
            onClick={() => void historyPage()}>
            <Icon name="clock" size={16} />
            Recent chats
          </button>
          <button
            className="drawer-row drawer-danger"
            disabled={inputBusy}
            onClick={async () => {
              if (
                window.confirm(
                  "Delete this conversation? Existing tasks and drafts will remain in Recent chats."
                )
              ) {
                await c.deleteChat()
                setMenu(false)
              }
            }}>
            <Icon name="trash" size={16} />
            Delete this conversation
          </button>
          <p className="drawer-section">Connectors</p>
          <ConnectorList
            capabilities={capabilities}
            connecting={connecting}
            onConnect={(id) => void connect(id)}
            onSelect={(id) => {
              openSettings(id)
              setMenu(false)
            }}
          />
          {connectError && (
            <p className="connector-error" role="alert">
              {connectError}
            </p>
          )}
          <div className="menu-account">
            {account && (
              <div className="account-card" role="group" aria-label="Account">
                <div className="account-card-head">
                  <span className="avatar" aria-hidden="true">
                    {initials}
                  </span>
                  <span className="menu-account-name">
                    <b>{displayName}</b>
                    <small title={user.email}>{user.email}</small>
                  </span>
                </div>
                <button
                  aria-label="Settings"
                  onClick={() => {
                    openSettings()
                    setAccount(false)
                    setMenu(false)
                  }}>
                  <Icon name="settings" size={16} />
                  Settings
                </button>
                <button
                  aria-label={
                    dark
                      ? "Switch to light appearance"
                      : "Switch to dark appearance"
                  }
                  onClick={theme}>
                  <Icon name={dark ? "sun" : "moon"} size={16} />
                  {dark ? "Light mode" : "Dark mode"}
                </button>
                <button
                  className="account-signout"
                  aria-label="Sign out"
                  onClick={logout}>
                  <Icon name="signout" size={16} />
                  Sign out
                </button>
              </div>
            )}
            <button
              className="account-button"
              aria-label="Account menu"
              aria-expanded={account}
              onClick={() => setAccount(!account)}>
              <span className="avatar" aria-hidden="true">
                {initials}
              </span>
              <b>{displayName}</b>
              <Icon name="chevron" size={14} />
            </button>
          </div>
        </nav>
      )}
      <div
        className="conversation"
        ref={scroll}
        onScroll={() => {
          const e = scroll.current
          atBottom.current =
            !e || e.scrollHeight - e.scrollTop - e.clientHeight < 100
        }}>
        {history && (
          <section className="history-surface">
            <div className="card-heading">
              <h2>Recent chats</h2>
              <button
                className="icon-button"
                aria-label="Close history"
                onClick={() => setHistory(null)}>
                <Icon name="close" />
              </button>
            </div>
            {recent.length === 0 && <p>Your earlier chats will appear here.</p>}
            {recent.map(({ key, context, latest: t, count }) => (
              <button
                className="history-row"
                key={key}
                disabled={inputBusy}
                onClick={() => {
                  void c.loadTask(t)
                  setHistory(null)
                }}>
                <span className="history-text">
                  <span className="history-title">
                    {context
                      ? subjects[context] || "Email conversation"
                      : t.instruction}
                  </span>
                  {context && (
                    <small>
                      {count > 1 ? `${count} requests · ` : ""}
                      {t.instruction}
                    </small>
                  )}
                </span>
                <Icon name="chevron" size={14} />
              </button>
            ))}
            {historyCursor && (
              <button onClick={() => void historyPage(true)}>Load more</button>
            )}
          </section>
        )}
        {!c.entries.length && !history && !contextOpen && (
          <section className="conversation-start">
            <span className="welcome-mark">
              <Icon name="sparkle" size={29} />
            </span>
            <h1>
              {firstName ? `${firstName}, shall we?` : "What’s on your mind?"}
            </h1>
            <p className="start-subtitle">
              {c.selection
                ? "A fresh perspective on what’s in front of you."
                : "Your day, a little lighter."}
            </p>
            <div className="suggestions" aria-label="Suggested actions">
              {c.selection ? (
                <>
                  <button
                    disabled={inputBusy}
                    onClick={() => suggest("Summarise this thread.")}>
                    <Icon name="sparkle" />
                    Summarise this thread
                    <Icon name="chevron" size={14} />
                  </button>
                  <button
                    disabled={inputBusy}
                    onClick={() => suggest("Draft a reply to this thread.")}>
                    <Icon name="edit" />
                    Draft a reply to this thread
                    <Icon name="chevron" size={14} />
                  </button>
                </>
              ) : (
                <>
                  <button
                    disabled={inputBusy || !openEmail}
                    onClick={() => void c.selectActive()}>
                    <GmailIcon />
                    Ask about the open email
                    <Icon name="chevron" size={14} />
                  </button>
                  <button
                    disabled={inputBusy}
                    onClick={() => {
                      setMessage("Find an email about ")
                      input.current?.focus()
                      setSources(false)
                    }}>
                    <Icon name="search" />
                    Find an email
                    <Icon name="chevron" size={14} />
                  </button>
                  <button
                    disabled={inputBusy}
                    onClick={() => {
                      setMessage("Write an email ")
                      input.current?.focus()
                    }}>
                    <Icon name="edit" />
                    Write an email
                    <Icon name="chevron" size={14} />
                  </button>
                </>
              )}
            </div>
          </section>
        )}
        {c.entries.map((entry) => (
          <TaskCard
            key={entry.id}
            entry={entry}
            controller={{
              ...c,
              openCalendarSetup: () => openSettings("calendar")
            }}
          />
        ))}
        <div ref={last} />
      </div>
      {c.error && (
        <p role="alert" className="global-error">
          {c.error}
          <button
            className="icon-button"
            aria-label="Dismiss error"
            onClick={() => c.setError("")}>
            <Icon name="close" size={14} />
          </button>
        </p>
      )}
      <div className="composer-wrap">
        {c.restoreFailed && (
          <p className="pending-context-note" role="alert">
            This conversation could not be loaded. Reopen Threadly to try again,
            or use New chat to start fresh.
          </p>
        )}
        {c.pendingUsesHiddenEmail && (
          <p className="pending-context-note" role="status">
            The unfinished request still includes its original email. Retry it
            or start a new conversation before changing the source.
          </p>
        )}
        {contextOpen && c.selection && (
          <ContextPicker
            selection={c.selection}
            close={() => setContextOpen(false)}
          />
        )}
        {c.contextBlocked && !c.restoreFailed && (
          <div className="context-recovery" role="alert">
            <div>
              <b>Email context needs attention</b>
              <span>
                The email saved with this conversation changed or is no longer
                available.
              </span>
            </div>
            <button
              disabled={inputBusy || c.contextLocked}
              onClick={() => void c.selectActive()}>
              Use open email
            </button>
            <button
              disabled={inputBusy || c.contextLocked}
              onClick={c.clearContext}>
              Continue without email
            </button>
          </div>
        )}
        {sources && (
          <div className="source-menu" aria-label="Add context">
            <button
              disabled={inputBusy || c.contextLocked}
              onClick={() => {
                void c.selectActive()
                setSources(false)
              }}>
              <GmailIcon />
              Use open Gmail thread
            </button>
            <button
              disabled={inputBusy || c.contextLocked}
              onClick={() => {
                setMessage("Find an email about ")
                input.current?.focus()
                setSources(false)
                setContextOpen(false)
              }}>
              <Icon name="search" />
              Find an email
            </button>
            {c.selection && (
              <button
                disabled={inputBusy || c.contextLocked}
                onClick={() => {
                  c.setSelection(null)
                  setSources(false)
                  setContextOpen(false)
                }}>
                Remove email context
              </button>
            )}
          </div>
        )}
        {approvalInfo && (
          <div className="approval-info" role="status">
            <Icon name="shield" />
            <span>
              Suggestions first. You review the exact email or event before
              anything is sent or booked.
            </span>
            <button
              className="icon-button"
              aria-label="Close approval info"
              onClick={() => setApprovalInfo(false)}>
              <Icon name="close" size={14} />
            </button>
          </div>
        )}
        <form className="composer" onSubmit={send}>
          {c.selection && (
            <div
              className="composer-context"
              role="group"
              aria-label="Attached email">
              <button
                type="button"
                className="context-chip"
                disabled={c.busy}
                title={[
                  c.selection.thread.subject,
                  c.selection.messages.find(
                    (m) => m.gmail_msg_id === c.selection.targetId
                  )?.from_addr || c.selection.messages[0]?.from_addr
                ]
                  .filter(Boolean)
                  .join(" · ")}
                aria-label={`Attached email: ${c.selection.thread.subject}. View details`}
                aria-expanded={contextOpen}
                onClick={() => setContextOpen(!contextOpen)}>
                <GmailIcon size={15} />
                <span>{c.selection.thread.subject}</span>
              </button>
              <button
                type="button"
                className="context-remove icon-button"
                disabled={inputBusy || c.contextLocked}
                aria-label="Detach email context"
                title={
                  c.contextLocked
                    ? "Retry the unfinished message or start a new conversation first"
                    : "Detach email; the next message clears the saved source"
                }
                onClick={() => {
                  c.clearContext()
                  setContextOpen(false)
                  setSources(false)
                  input.current?.focus()
                }}>
                <Icon name="close" size={14} />
              </button>
            </div>
          )}
          <textarea
            ref={input}
            aria-label="Your request"
            placeholder={
              latest?.task?.state === "needs_clarification"
                ? "Reply or ask a follow-up…"
                : latest?.artifacts?.some((a) => a.artifact.kind === "draft")
                  ? "Edit the reply or ask a question…"
                  : "Ask your inbox…"
            }
            value={message}
            rows={1}
            maxLength={4000}
            disabled={inputBusy}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => {
              if (
                e.key === "Enter" &&
                !e.shiftKey &&
                !e.nativeEvent.isComposing
              ) {
                e.preventDefault()
                send()
              }
            }}
          />
          <div className="composer-actions">
            <button
              type="button"
              className="icon-button"
              aria-label="Add context"
              title="Add context"
              aria-expanded={sources}
              disabled={inputBusy || c.contextLocked}
              onClick={() => setSources(!sources)}>
              <Icon name="plus" />
            </button>
            <span className="composer-hint">
              {recording ? "Listening…" : c.busy ? "Working on it…" : ""}
            </span>
            <button
              type="button"
              className={`icon-button${recording ? " recording" : ""}`}
              aria-label="Talk to Threadly"
              title="Talk to Threadly"
              disabled={inputBusy}
              onClick={dictate}>
              <Icon name="mic" size={17} />
            </button>
            <button
              type="button"
              className="icon-button"
              aria-label="Approval information"
              title="You’re in control"
              onClick={() => setApprovalInfo(!approvalInfo)}>
              <Icon name="shield" size={17} />
            </button>
            <button
              className="send-button"
              type="submit"
              title="Send request"
              aria-label="Send request"
              disabled={inputBusy || !message.trim()}>
              <Icon name="send" size={18} />
            </button>
          </div>
        </form>
        <p className="composer-footnote">
          Threadly can make mistakes. Review important details.
        </p>
      </div>
      {recording && <VoiceOrb respond={voiceRespond} onClose={voiceDone} />}
    </>
  )
}
