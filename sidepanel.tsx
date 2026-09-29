import { useEffect, useRef, useState } from "react"

import "./style.css"

import { ContextPicker } from "./components/ContextPicker"
import { Icon, Logo } from "./components/Icon"
import { Settings } from "./components/Settings"
import { TaskCard } from "./components/TaskCard"
import { api, bridge, errorText } from "./lib/api"
import { gmailUrlShowsEmail } from "./lib/gmail-context"
import type { Capability, User } from "./lib/types"
import { useAssistant } from "./lib/use-assistant"

export default function SidePanel() {
  const [user, setUser] = useState<User | null>(null),
    [capabilities, setCapabilities] = useState<Capability[]>([]),
    [ready, setReady] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [settings, setSettings] = useState(false),
    [dark, setDark] = useState(false)
  const refresh = async () => {
    const s = await bridge<any>({ type: "STATUS" })
    setUser(s.user)
    if (s.user) {
      try {
        const c = await api("/assistant/capabilities")
        setCapabilities(c.capabilities)
      } catch (e) {
        if (e.status === 401) setUser(null)
        throw e
      }
    } else setCapabilities([])
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
        setUser(null)
        setCapabilities([])
      }
    }
    chrome.storage.onChanged.addListener(changed)
    return () => chrome.storage.onChanged.removeListener(changed)
  }, [])
  const login = async () => {
    setBusy(true)
    setError("")
    try {
      await bridge({ type: "LOGIN" })
      await refresh()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  const logout = async () => {
    await bridge({ type: "LOGOUT" })
    setUser(null)
    setCapabilities([])
    setSettings(false)
    setError("")
  }
  const theme = () => {
    setDark(!dark)
    void chrome.storage.local.set({ darkMode: !dark })
  }
  return (
    <main className={dark ? "threadly dark" : "threadly"}>
      {(!user || settings) && (
        <header className="app-header">
          <span className="wordmark">
            <Icon name="sparkle" />
            Threadly
          </span>
          <button
            className="icon-button"
            aria-label={settings ? "Close settings" : "Settings"}
            title={settings ? "Close settings" : "Settings"}
            onClick={() => setSettings(!settings)}>
            <Icon name={settings ? "close" : "menu"} />
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
              user={user}
              capabilities={capabilities}
              onAuth={refresh}
              onClose={() => setSettings(false)}
              onPreferences={() => {}}
            />
          )}
          {user && (
            <div className="assistant-host" hidden={settings}>
              <Assistant
                key={user.id}
                user={user}
                capabilities={capabilities}
                openSettings={() => setSettings(true)}
                logout={logout}
                theme={theme}
                dark={dark}
              />
            </div>
          )}
          {!user && !settings && (
            <section className="welcome">
              <div>
                <span className="welcome-mark">
                  <Icon name="sparkle" size={32} />
                </span>
                <h1>
                  A little less work.
                  <br />A little more flow.
                </h1>
                <p>Your email, your calendar, one conversation.</p>
                <button
                  className="primary login-button"
                  disabled={busy}
                  onClick={login}>
                  {busy ? "Connecting…" : "Sign in with Google"}
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
// Friendly names for the backend capability ids shown as connectors.
const connectorNames: Record<string, string> = {
  gmail_read: "Gmail",
  calendar_read: "Google Calendar",
  calendar_write: "Calendar booking"
}
function Assistant({
  user,
  capabilities,
  openSettings,
  logout,
  theme,
  dark
}: {
  user: User
  capabilities: Capability[]
  openSettings: () => void
  logout: () => void
  theme: () => void
  dark: boolean
}) {
  const c = useAssistant(user),
    [message, setMessage] = useState(""),
    [menu, setMenu] = useState(false),
    [sources, setSources] = useState(false),
    [contextOpen, setContextOpen] = useState(false),
    [history, setHistory] = useState<any>(null),
    [historyCursor, setHistoryCursor] = useState<string | null>(null),
    [recording, setRecording] = useState(false),
    [openEmail, setOpenEmail] = useState(false),
    [approvalInfo, setApprovalInfo] = useState(false)
  const recognition = useRef<any>(null),
    last = useRef<HTMLDivElement>(null),
    input = useRef<HTMLTextAreaElement>(null),
    scroll = useRef<HTMLDivElement>(null),
    atBottom = useRef(true),
    positionedSearch = useRef<string | null>(null)
  const startupBusy = c.busy || c.restoring
  const inputBusy = startupBusy || c.restoreFailed
  useEffect(() => {
    void c.selectActive(true)
    return () => recognition.current?.abort()
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
  const dictate = () => {
    if (recording) {
      recognition.current?.stop()
      return
    }
    const Recognition =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition
    if (!Recognition) {
      c.setError(
        "Dictation isn’t available in this browser. You can type your request."
      )
      return
    }
    const r = new Recognition()
    recognition.current = r
    r.lang = navigator.language
    r.interimResults = false
    r.continuous = false
    r.onresult = (e: any) =>
      setMessage(
        (old) => `${old}${old ? " " : ""}${e.results[0][0].transcript}`
      )
    r.onerror = () => {
      c.setError(
        "Couldn’t start dictation. Check microphone access or type your request."
      )
      setRecording(false)
    }
    r.onend = () => setRecording(false)
    try {
      r.start()
      setRecording(true)
    } catch {
      c.setError("Couldn’t start the microphone.")
      setRecording(false)
    }
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
  // Sending is an approval step, not a connector, so it isn't listed here.
  const connectors = capabilities.filter((tool) => !tool.id.endsWith("_send"))
  // Recent chats leaves out what is already open here and repeats of a request.
  const openTasks = new Set(c.entries.map((e) => e.task?.task_id))
  const seen = new Set<string>()
  const recent = (history || []).filter((t) => {
    const text = t.instruction?.trim().toLowerCase()
    if (openTasks.has(t.task_id) || seen.has(text)) return false
    seen.add(text)
    return true
  })
  const firstName = user.name?.trim().split(/\s+/)[0] || ""
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
          onClick={() => setMenu(!menu)}>
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
          {connectors.length > 0 && (
            <>
              <p className="drawer-section">Connectors</p>
              <ul className="connector-list">
                {connectors.map((tool) => (
                  <li key={tool.id}>
                    <span className="connector-icon">
                      <Icon
                        name={
                          tool.id.startsWith("calendar")
                            ? "calendar"
                            : tool.id.endsWith("send")
                              ? "send"
                              : "mail"
                        }
                        size={15}
                      />
                    </span>
                    <span className="connector-name">
                      {connectorNames[tool.id] || tool.id.replaceAll("_", " ")}
                    </span>
                    <span
                      className={`connector-status${tool.ready ? " is-on" : ""}`}>
                      {tool.ready
                        ? "Connected"
                        : tool.status === "disabled"
                          ? "Off"
                          : "Not connected"}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
          <div className="menu-account">
            <span className="avatar" aria-hidden="true">
              {initials}
            </span>
            <span className="menu-account-name">
              <b>{user.name || user.email.split("@")[0]}</b>
              <small>{user.email}</small>
            </span>
            <span className="menu-account-actions">
              <button
                className="icon-button"
                aria-label={
                  dark
                    ? "Switch to light appearance"
                    : "Switch to dark appearance"
                }
                title={dark ? "Light appearance" : "Dark appearance"}
                onClick={theme}>
                <Icon name={dark ? "sun" : "moon"} size={16} />
              </button>
              <button
                className="icon-button"
                aria-label="Settings"
                title="Settings"
                onClick={() => {
                  openSettings()
                  setMenu(false)
                }}>
                <Icon name="settings" size={16} />
              </button>
              <button
                className="icon-button"
                aria-label="Sign out"
                title="Sign out"
                onClick={logout}>
                <Icon name="signout" size={16} />
              </button>
            </span>
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
            {recent.map((t) => (
              <button
                className="history-row"
                key={t.task_id}
                disabled={inputBusy}
                onClick={() => {
                  void c.loadTask(t)
                  setHistory(null)
                }}>
                <span>{t.instruction}</span>
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
                    <Icon name="mail" />
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
          <TaskCard key={entry.id} entry={entry} controller={c} />
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
            onChange={c.setSelection}
            close={() => setContextOpen(false)}
            busy={inputBusy || c.contextLocked}
            rewrite={(id) => {
              void c.submit(
                "Rewrite the selected message to be clearer and more concise, preserving its meaning.",
                id
              )
            }}
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
              <Icon name="mail" />
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
                title="View attached email details"
                aria-label={`Attached email: ${c.selection.thread.subject}. View details`}
                aria-expanded={contextOpen}
                onClick={() => setContextOpen(!contextOpen)}>
                <Icon name="mail" size={15} />
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
              aria-label={recording ? "Stop dictation" : "Dictate request"}
              title="Dictate request"
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
    </>
  )
}
