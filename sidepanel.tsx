import { useEffect, useRef, useState } from "react"

import "./style.css"

import { ContextPicker } from "./components/ContextPicker"
import { Icon } from "./components/Icon"
import { Settings } from "./components/Settings"
import { TaskCard } from "./components/TaskCard"
import { api, bridge, errorText } from "./lib/api"
import type { Capability, User } from "./lib/types"
import { useAssistant } from "./lib/use-assistant"

export default function SidePanel() {
  const [user, setUser] = useState<User | null>(null),
    [capabilities, setCapabilities] = useState<Capability[]>([]),
    [ready, setReady] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [settings, setSettings] = useState(false),
    [dark, setDark] = useState(true)
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
      .then((s) => setDark(s.darkMode !== false))
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
function Assistant({
  user,
  openSettings,
  logout,
  theme,
  dark
}: {
  user: User
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
    [approvalInfo, setApprovalInfo] = useState(false)
  const recognition = useRef<any>(null),
    last = useRef<HTMLDivElement>(null),
    input = useRef<HTMLTextAreaElement>(null),
    scroll = useRef<HTMLDivElement>(null),
    atBottom = useRef(true),
    positionedSearch = useRef<string | null>(null)
  useEffect(() => {
    void c.selectActive(true)
    return () => recognition.current?.abort()
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
    if (c.busy || !message.trim()) return
    const text = message
    setMessage("")
    setSources(false)
    atBottom.current = true
    void c.submit(text)
  }
  const suggest = (text: string) => {
    if (c.busy) return
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
  const firstName = user.name?.trim().split(/\s+/)[0] || ""
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
          disabled={c.busy}
          onClick={() => void newChat()}>
          <Icon name="edit" />
        </button>
      </header>
      {menu && (
        <nav className="panel-menu" aria-label="Conversation menu">
          <div className="drawer-title">
            <span>Threadly</span>
            <button
              className="icon-button"
              aria-label="Close conversation menu"
              onClick={() => setMenu(false)}>
              <Icon name="close" />
            </button>
          </div>
          <button onClick={() => void newChat()} disabled={c.busy}>
            <Icon name="edit" />
            New conversation
          </button>
          <button
            disabled={c.busy}
            onClick={async () => {
              if (
                window.confirm(
                  "Delete this conversation? Existing tasks and drafts will remain in Recent work."
                )
              ) {
                await c.deleteChat()
                setMenu(false)
              }
            }}>
            Delete this conversation
          </button>
          <button onClick={() => void historyPage()}>
            <Icon name="clock" />
            Recent work
          </button>
          <button
            onClick={() => {
              openSettings()
              setMenu(false)
            }}>
            Settings
          </button>
          <button onClick={theme}>
            {dark ? "Switch to light appearance" : "Switch to dark appearance"}
          </button>
          <div className="menu-account">
            <small>{user.email}</small>
            <button onClick={logout}>Sign out</button>
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
        {contextOpen && c.selection && (
          <ContextPicker
            selection={c.selection}
            onChange={c.setSelection}
            close={() => setContextOpen(false)}
            busy={c.busy}
            rewrite={(id) => {
              void c.submit(
                "Rewrite the selected message to be clearer and more concise, preserving its meaning.",
                id
              )
            }}
          />
        )}
        {history && (
          <section className="history-surface">
            <div className="card-heading">
              <h2>Recent work</h2>
              <button
                className="icon-button"
                aria-label="Close history"
                onClick={() => setHistory(null)}>
                <Icon name="close" />
              </button>
            </div>
            {history.length === 0 && (
              <p>
                Your generated drafts, summaries and plans will appear here.
              </p>
            )}
            {history.map((t) => (
              <button
                className="history-row"
                key={t.task_id}
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
                    disabled={c.busy}
                    onClick={() => suggest("Summarise this thread.")}>
                    <Icon name="sparkle" />
                    Summarise this for me
                    <Icon name="chevron" size={14} />
                  </button>
                  <button
                    disabled={c.busy}
                    onClick={() => suggest("Draft a reply to this thread.")}>
                    <Icon name="edit" />
                    Help me reply
                    <Icon name="chevron" size={14} />
                  </button>
                  <button
                    disabled={c.busy}
                    onClick={() =>
                      suggest("What needs my attention in this email?")
                    }>
                    <Icon name="check" />
                    What needs my attention?
                    <Icon name="chevron" size={14} />
                  </button>
                </>
              ) : (
                <>
                  <button
                    disabled={c.busy}
                    onClick={() => void c.selectActive()}>
                    <Icon name="mail" />
                    Ask about the open email
                    <Icon name="chevron" size={14} />
                  </button>
                  <button
                    onClick={() => {
                      setMessage("Show me emails about ")
                      input.current?.focus()
                      setSources(false)
                    }}>
                    <Icon name="search" />
                    Find an email
                    <Icon name="chevron" size={14} />
                  </button>
                  <button
                    onClick={() => {
                      setMessage("Write an email ")
                      input.current?.focus()
                    }}>
                    <Icon name="edit" />
                    Find the right words
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
        {c.contextBlocked && (
          <div className="context-recovery" role="alert">
            <div>
              <b>Email context needs attention</b>
              <span>
                The email saved with this conversation changed or is no longer
                available.
              </span>
            </div>
            <button
              disabled={c.busy}
              onClick={() => void c.selectActive()}>
              Use open email
            </button>
            <button disabled={c.busy} onClick={c.clearContext}>
              Continue without email
            </button>
          </div>
        )}
        {sources && (
          <div className="source-menu" aria-label="Add context">
            <button
              disabled={c.busy}
              onClick={() => {
                void c.selectActive()
                setSources(false)
              }}>
              <Icon name="mail" />
              Use open Gmail thread
            </button>
            <button
              disabled={c.busy}
              onClick={() => {
                setMessage("Show me emails about ")
                input.current?.focus()
                setSources(false)
                setContextOpen(false)
              }}>
              <Icon name="search" />
              Find an email
            </button>
            {c.selection && (
              <button
                disabled={c.busy}
                onClick={() => {
                  c.setSelection(null)
                  setSources(false)
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
        {c.selection && (
          <button
            type="button"
            className="context-chip"
            disabled={c.busy}
            title="View email context"
            aria-label={`Email context: ${c.selection.thread.subject}`}
            aria-expanded={contextOpen}
            onClick={() => {
              setContextOpen(!contextOpen)
            }}>
            <Icon name="mail" size={14} />
            <span>{c.selection.thread.subject}</span>
            <small>
              {c.selection.selectedIds.length}{" "}
              {c.selection.selectedIds.length === 1 ? "message" : "messages"}
            </small>
          </button>
        )}
        <form className="composer" onSubmit={send}>
          <textarea
            ref={input}
            aria-label="Your request"
            placeholder={
              latest?.task?.state === "needs_clarification"
                ? "Reply or ask a follow-up…"
                : "Ask your inbox…"
            }
            value={message}
            rows={1}
            maxLength={4000}
            disabled={c.busy}
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
              disabled={c.busy}
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
              disabled={c.busy}
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
              disabled={c.busy || !message.trim()}>
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
