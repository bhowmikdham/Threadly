import { useEffect, useRef, useState } from "react"

import "./style.css"

import { MailSearch } from "./components/MailSearch"
import { Settings } from "./components/Settings"
import { TaskCard } from "./components/TaskCard"
import { api, bridge, errorText } from "./lib/api"
import type { Capability, Mode, User } from "./lib/types"
import { useAssistant } from "./lib/use-assistant"

export default function SidePanel() {
  const [user, setUser] = useState<User | null>(null),
    [capabilities, setCapabilities] = useState<Capability[]>([]),
    [ready, setReady] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [settings, setSettings] = useState(false),
    [dark, setDark] = useState(false),
    [sessionKey, setSessionKey] = useState(0)
  const refresh = async () => {
    const s = await bridge<any>({ type: "STATUS" })
    setUser(s.user)
    setSessionKey((n) => n + 1)
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
    void chrome.storage.local.get("darkMode").then((s) => setDark(!!s.darkMode))
  }, [])
  useEffect(() => {
    const changed = (changes: any, area: string) => {
      if (
        area === "session" &&
        changes.threadlySession &&
        !changes.threadlySession.newValue
      ) {
        setUser(null)
        setCapabilities([])
        setSessionKey((n) => n + 1)
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
    setSessionKey((n) => n + 1)
    setSettings(false)
  }
  return (
    <main className={dark ? "threadly dark" : "threadly"}>
      <header className="app-header">
        <strong>Threadly</strong>
        <div>
          <button
            aria-label="Toggle dark mode"
            onClick={() => {
              setDark(!dark)
              void chrome.storage.local.set({ darkMode: !dark })
            }}>
            {dark ? "☀" : "☾"}
          </button>
          <button aria-label="Settings" onClick={() => setSettings(!settings)}>
            ⚙
          </button>
        </div>
      </header>
      {!ready ? (
        <p className="empty" role="status">
          Connecting…
        </p>
      ) : settings ? (
        <Settings
          user={user}
          capabilities={capabilities}
          onAuth={refresh}
          onClose={() => setSettings(false)}
          onPreferences={() => {}}
        />
      ) : user ? (
        <Assistant
          key={`${user.id}:${sessionKey}`}
          user={user}
          capabilities={capabilities}
          openSettings={() => setSettings(true)}
        />
      ) : (
        <section className="welcome">
          <div>
            <h1>Welcome to Threadly</h1>
            <p>
              Search your mail, summarise threads, plan meetings and draft
              replies—with you in control.
            </p>
            <button className="primary" disabled={busy} onClick={login}>
              {busy ? "Signing in…" : "Sign in with Google"}
            </button>
            <p className="muted">
              Your Google connection is managed by Threadly’s backend. No
              mailbox import is needed.
            </p>
          </div>
        </section>
      )}
      {error && (
        <p className="global-error" role="alert">
          {error}
        </p>
      )}
      <footer>
        <span className="avatar">
          {(user?.name || user?.email || "G").slice(0, 2).toUpperCase()}
        </span>
        <span className="account">{user?.email || "Guest"}</span>
        {user && <button onClick={logout}>Sign out</button>}
      </footer>
    </main>
  )
}
function Assistant({
  user,
  capabilities,
  openSettings
}: {
  user: User
  capabilities: Capability[]
  openSettings: () => void
}) {
  const c = useAssistant(user),
    [message, setMessage] = useState(""),
    [mode, setMode] = useState<Mode>("ask"),
    [to, setTo] = useState(""),
    [replyInWorkflow, setReplyInWorkflow] = useState(false),
    [search, setSearch] = useState(false),
    [history, setHistory] = useState<any>(null),
    [historyCursor, setHistoryCursor] = useState<string | null>(null),
    [recording, setRecording] = useState(false)
  const recognition = useRef<any>(null),
    last = useRef<HTMLDivElement>(null)
  useEffect(() => {
    last.current?.scrollIntoView({ behavior: "smooth", block: "end" })
  }, [c.entries])
  useEffect(
    () => () => {
      recognition.current?.abort()
    },
    []
  )
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
    } catch (e) {
      c.setError(errorText(e))
    }
  }
  const send = (e?: React.FormEvent) => {
    e?.preventDefault()
    if (c.busy || !message.trim()) return
    const text = message
    setMessage("")
    void c.submit(text, mode, to, replyInWorkflow)
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
        "Dictation is unavailable in this browser. You can type your request."
      )
      return
    }
    const r = new Recognition()
    recognition.current = r
    r.lang = navigator.language
    r.interimResults = false
    r.continuous = false
    r.onresult = (e: any) => {
      setMessage(
        (old) => `${old}${old ? " " : ""}${e.results[0][0].transcript}`
      )
    }
    r.onerror = () => {
      c.setError(
        "Dictation could not start. Check microphone permission or type your request."
      )
      setRecording(false)
    }
    r.onend = () => setRecording(false)
    try {
      r.start()
      setRecording(true)
    } catch {
      c.setError("Microphone could not start.")
      setRecording(false)
    }
  }
  const chooseMode = (m: Mode) => {
    setMode(m)
    if (m === "summarise") setMessage("Summarise this thread.")
    if (m === "reply") setMessage("Draft a reply to this thread.")
    if (m === "compose") setMessage("")
    if (m === "workflow") setMessage("")
  }
  return (
    <>
      <nav className="toolbar">
        <button disabled={c.busy} onClick={c.newChat}>
          ＋ New chat
        </button>
        <button onClick={() => void historyPage()}>History</button>
        <button onClick={() => setSearch(!search)}>Search mail</button>
      </nav>
      <div className="conversation">
        {search && (
          <MailSearch
            select={async (id) => {
              await c.selectThread(id)
              setSearch(false)
            }}
          />
        )}
        {history && (
          <section className="artifact">
            <div className="card-heading">
              <b>Recent tasks</b>
              <button onClick={() => setHistory(null)}>Close</button>
            </div>
            {history.length === 0 && <p>No tasks yet.</p>}
            {history.map((t) => (
              <button
                className="history-row"
                key={t.task_id}
                onClick={() => {
                  void c.loadTask(t)
                  setHistory(null)
                }}>
                <span>{t.instruction}</span>
                <small>{t.state.replaceAll("_", " ")}</small>
              </button>
            ))}
            {historyCursor && (
              <button onClick={() => void historyPage(true)}>Load more</button>
            )}
          </section>
        )}
        <section className="context">
          <div className="button-row">
            <button disabled={c.busy} onClick={c.selectActive}>
              Use open Gmail thread
            </button>
            {c.selection && (
              <button disabled={c.busy} onClick={() => c.setSelection(null)}>
                Clear selection
              </button>
            )}
          </div>
          {c.selection && (
            <details open>
              <summary>{c.selection.thread.subject}</summary>
              <p className="muted">
                Choose the messages to include. Reply and rewrite require one
                explicit target. Message numbers refer only to the included
                messages.
              </p>
              {c.selection.messages.map((m, i) => (
                <div className="message-choice" key={m.gmail_msg_id}>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={c.selection.selectedIds.includes(m.gmail_msg_id)}
                      onChange={(e) => {
                        const s = c.selection
                        c.setSelection({
                          ...s,
                          selectedIds: e.target.checked
                            ? [...s.selectedIds, m.gmail_msg_id]
                            : s.selectedIds.filter(
                                (id) => id !== m.gmail_msg_id
                              ),
                          targetId:
                            !e.target.checked && s.targetId === m.gmail_msg_id
                              ? null
                              : s.targetId
                        })
                      }}
                    />
                    <span>
                      {c.selection.selectedIds.includes(m.gmail_msg_id)
                        ? `Message ${c.selection.messages.slice(0, i + 1).filter((item) => c.selection.selectedIds.includes(item.gmail_msg_id)).length}`
                        : "Excluded message"}{" "}
                      · {m.from_addr}
                      <small>
                        {m.sent_at || m.received_at
                          ? new Date(
                              m.sent_at || m.received_at
                            ).toLocaleString()
                          : ""}
                      </small>
                    </span>
                  </label>
                  <button
                    aria-pressed={c.selection.targetId === m.gmail_msg_id}
                    onClick={() => {
                      const s = c.selection
                      c.setSelection({
                        ...s,
                        targetId: m.gmail_msg_id,
                        selectedIds: [
                          ...new Set([...s.selectedIds, m.gmail_msg_id])
                        ]
                      })
                      const match = m.from_addr?.match(/<([^>]+)>/)
                      setTo(match?.[1] || m.from_addr || "")
                    }}>
                    {c.selection.targetId === m.gmail_msg_id
                      ? "Target selected"
                      : "Use as target"}
                  </button>
                </div>
              ))}
            </details>
          )}
        </section>
        {c.entries.length === 0 && !history && !search && (
          <div className="empty">
            <h1>Welcome to Threadly</h1>
            <p>
              Ask about a selected email, prepare a draft, or review a plan for
              several steps.
            </p>
            <div className="button-row">
              <button onClick={() => chooseMode("summarise")}>Summarise</button>
              <button onClick={() => chooseMode("reply")}>Draft reply</button>
              <button onClick={() => chooseMode("compose")}>Compose</button>
              <button onClick={() => chooseMode("workflow")}>
                Plan / schedule
              </button>
            </div>
          </div>
        )}
        {c.entries.map((entry) => (
          <TaskCard key={entry.id} entry={entry} controller={c} />
        ))}
        <div ref={last} />
      </div>
      {c.error && (
        <p role="alert" className="global-error">
          {c.error}
        </p>
      )}
      <form className="composer" onSubmit={send}>
        <div className="button-row">
          <label className="mode">
            Request
            <select
              aria-label="Request type"
              value={mode}
              onChange={(e) => chooseMode(e.target.value as Mode)}>
              <option value="ask">Ask / automatic routing</option>
              <option value="summarise">Summarise</option>
              <option value="reply">Reply draft</option>
              <option value="compose">New email</option>
              <option value="workflow">Plan / schedule / multiple steps</option>
              <option value="transform">Rewrite selected message</option>
            </select>
          </label>
          {mode === "workflow" && (
            <button type="button" onClick={openSettings}>
              Calendar settings
            </button>
          )}
        </div>
        {["reply", "compose", "workflow"].includes(mode) && (
          <label>
            Recipient
            {mode === "workflow" ? " (if the plan includes a draft)" : ""}
            <input
              type="text"
              placeholder="name@example.com"
              value={to}
              onChange={(e) => setTo(e.target.value)}
            />
          </label>
        )}
        {mode === "workflow" && (
          <label className="check">
            <input
              type="checkbox"
              checked={replyInWorkflow}
              onChange={(e) => setReplyInWorkflow(e.target.checked)}
            />
            Draft should reply to the selected target message
          </label>
        )}
        {mode === "compose" && c.selection && (
          <p className="muted">
            New-email mode uses your instruction without the selected thread.
          </p>
        )}
        <textarea
          aria-label="Your request"
          placeholder="Assign a task or ask anything…"
          value={message}
          rows={3}
          maxLength={mode === "workflow" ? 4000 : 8000}
          disabled={c.busy}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault()
              send()
            }
          }}
        />
        <div className="composer-actions">
          <button
            type="button"
            disabled={c.busy}
            aria-label={recording ? "Stop dictation" : "Dictate request"}
            onClick={dictate}>
            {recording ? "Stop microphone" : "Microphone"}
          </button>
          <span className="muted">
            {recording
              ? "Listening · review text before sending"
              : c.busy
                ? "Working…"
                : "Review suggestions before using them"}
          </span>
          <button
            className="primary"
            type="submit"
            disabled={c.busy || !message.trim()}>
            Submit
          </button>
        </div>
      </form>
    </>
  )
}
