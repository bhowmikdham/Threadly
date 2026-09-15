import { useState, useRef } from "react"
import "./style.css"

// API configuration constants
const ELEVENLABS_API_KEY = process.env.PLASMO_PUBLIC_ELEVENLABS_API_KEY;
const GEMINI_API_KEY = process.env.PLASMO_PUBLIC_GEMINI_API_KEY;
const BACKEND_BASE_URL = "http://localhost:8000"

// --- Interfaces mirroring backend app/schemas ---

interface ThreadMessage {
  id: string
  author: string
  date: string
  body: string
}

interface ThreadOption {
  id: string
  snippet: string
}

interface EvidenceItem {
  evidenceId: string
  messageId: string
  author: string
  date: string
  snippet: string
}

interface SummaryOutput {
  overview: string
  decisions: { text: string; status: "proposed" | "final"; evidenceIds: string[] }[]
  actions: { text: string; owner: string | null; due: string | null; evidenceIds: string[] }[]
  unresolvedQuestions: { text: string; evidenceIds: string[] }[]
  coverage: "complete" | "partial"
}

interface ChatMessage {
  id: string
  sender: "user" | "ai"
  text?: string
  threadsList?: ThreadOption[]
  summary?: SummaryOutput
  taskId?: string
}

interface ChatSession {
  id: string
  title: string
  history: ChatMessage[]
  createdAt: Date
}

function SidePanel() {
  const [theme, setTheme] = useState<"dark" | "light">("light")
  const [signedIn, setSignedIn] = useState<boolean>(false)
  const [email, setEmail] = useState<string>("")
  const [loading, setLoading] = useState<boolean>(false)

  const [message, setMessage] = useState<string>("")
  const [chatHistory, setChatHistory] = useState<ChatMessage[]>([])
  const [showAppsMenu, setShowAppsMenu] = useState<boolean>(false)

  const [savedSessions, setSavedSessions] = useState<ChatSession[]>([])
  const [showHistoryDrawer, setShowHistoryDrawer] = useState<boolean>(false)

  const [recording, setRecording] = useState<boolean>(false)
  const [transcribing, setTranscribing] = useState<boolean>(false)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  const [summarizing, setSummarizing] = useState<boolean>(false)
  const [error, setError] = useState<string | null>(null)

  const isDark = theme === "dark"
  const themeStyles = {
    bg: isDark ? "#121212" : "#FAF9F6",          
    headerBg: isDark ? "#1e1e1e" : "#F4F3EF",    
    drawerBg: isDark ? "#181818" : "#F4F3EF",    
    cardBg: isDark ? "#222222" : "#FFFFFF",      
    aiBubbleBg: isDark ? "#1e1e1e" : "#F4F3EF",  
    inputBg: isDark ? "#1e1e1e" : "#FFFFFF",     
    border: isDark ? "#2a2a2a" : "#E8E6E1",      
    text: isDark ? "#e0e0e0" : "#2c2b28",        
    textMuted: isDark ? "#aaaaaa" : "#787670",   
    textHeading: isDark ? "#ffffff" : "#2c2b28",
    buttonBg: isDark ? "#2a2a2a" : "#E8E6E1",    
    menuHoverBg: isDark ? "#2a2a2a" : "#F0EFEA",
    accent: "#e8590c"
  }

  const toggleTheme = () => setTheme((prev) => (prev === "dark" ? "light" : "dark"))

  const signIn = () => {
    setLoading(true)
    setError(null)
    chrome.identity.getAuthToken({ interactive: true }, async (token) => {
      if (chrome.runtime.lastError || !token) {
        console.error(chrome.runtime.lastError)
        setError("Failed to sign in with Google.")
        setLoading(false)
        return
      }

      try {
        const res = await fetch("https://www.googleapis.com/oauth2/v2/userinfo", {
          headers: { Authorization: `Bearer ${token}` }
        })
        const profile = await res.json()
        setEmail(profile.email)
        setSignedIn(true)
      } catch (err) {
        console.error("Userinfo fetch error:", err)
        setError("Could not retrieve user profile.")
      } finally {
        setLoading(false)
      }
    })
  }

  const transcribeAudio = async (audioBlob: Blob) => {
    setTranscribing(true)
    const formData = new FormData()
    formData.append("file", audioBlob, "recording.webm")
    formData.append("model_id", "scribe_v2")
    try {
      const res = await fetch("https://api.elevenlabs.io/v1/speech-to-text", {
        method: "POST",
        headers: { "xi-api-key": ELEVENLABS_API_KEY },
        body: formData
      })
      const data = await res.json()
      if (data.text) {
        setMessage((prev) => (prev ? prev + " " + data.text : data.text))
      }
    } catch (err) {
      console.error("Transcription failed: ", err)
    } finally {
      setTranscribing(false)
    }
  }

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" })
      chunksRef.current = []
      recorder.ondataavailable = (e) => chunksRef.current.push(e.data)
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        const audioBlob = new Blob(chunksRef.current, { type: recorder.mimeType })
        await transcribeAudio(audioBlob)
      }
      recorder.start()
      mediaRecorderRef.current = recorder
      setRecording(true)
    } catch (err) {
      console.error("Mic permission error:", err)
      alert("Microphone access was blocked or dismissed.")
    }
  }

  const stopRecording = () => {
    mediaRecorderRef.current?.stop()
    setRecording(false)
  }

  const toggleMic = () => (recording ? stopRecording() : startRecording())

  const decodeBase64Url = (data: string) => {
    const base64 = data.replace(/-/g, "+").replace(/_/g, "/")
    return decodeURIComponent(
      atob(base64)
        .split("")
        .map((c) => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2))
        .join("")
    )
  }

  const extractPlainText = (payload: any): string => {
    if (payload.mimeType === "text/plain" && payload.body?.data) {
      return decodeBase64Url(payload.body.data)
    }
    if (payload.parts) {
      for (const part of payload.parts) {
        const text = extractPlainText(part)
        if (text) return text
      }
    }
    return ""
  }

  const getHeader = (headers: any[], name: string) =>
    headers.find((h) => h.name === name)?.value || ""

  const fetchRecentThreads = async (token: string): Promise<ThreadOption[]> => {
    const listRes = await fetch(
      "https://gmail.googleapis.com/gmail/v1/users/me/threads?maxResults=5",
      { headers: { Authorization: `Bearer ${token}` } }
    )
    const listData = await listRes.json()
    return (listData.threads || []).map((t: any) => ({
      id: t.id,
      snippet: t.snippet || "No snippet available"
    }))
  }

  const fetchThreadMessagesById = async (token: string, threadId: string): Promise<ThreadMessage[]> => {
    const threadRes = await fetch(
      `https://gmail.googleapis.com/gmail/v1/users/me/threads/${threadId}?format=full`,
      { headers: { Authorization: `Bearer ${token}` } }
    )
    const threadData = await threadRes.json()

    return (threadData.messages || []).map((m: any) => ({
      id: m.id,
      author: getHeader(m.payload.headers, "From"),
      date: getHeader(m.payload.headers, "Date"),
      body: extractPlainText(m.payload) || m.snippet || ""
    }))
  }

  const buildEvidenceSnapshot = (messages: ThreadMessage[]): EvidenceItem[] => {
    return [...messages]
      .sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime())
      .map((m, i) => ({
        evidenceId: `ev_${i + 1}`,
        messageId: m.id,
        author: m.author,
        date: m.date,
        snippet: m.body.slice(0, 2000)
      }))
  }

  const generateSummary = async (evidence: EvidenceItem[]): Promise<SummaryOutput> => {
    const evidenceBlock = evidence
      .map((e) => `[${e.evidenceId}] ${e.author} (${e.date}): ${e.snippet}`)
      .join("\n\n")

    const prompt = `You summarise an email thread using ONLY the evidence provided.
Every decision, action, and unresolved question MUST cite one or more evidenceIds from the given list — never invent an evidenceId that isn't present.
If an owner or due date is not explicitly stated, use null — never guess.
Distinguish "proposed" decisions from "final" decisions.

Respond ONLY with valid JSON matching this exact structure:
{
  "overview": "string",
  "decisions": [{ "text": "string", "status": "proposed" | "final", "evidenceIds": ["string"] }],
  "actions": [{ "text": "string", "owner": "string or null", "due": "string or null", "evidenceIds": ["string"] }],
  "unresolvedQuestions": [{ "text": "string", "evidenceIds": ["string"] }],
  "coverage": "complete" | "partial"
}

Evidence:
${evidenceBlock}`

    const response = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key=${GEMINI_API_KEY}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contents: [{ parts: [{ text: prompt }] }],
          generationConfig: { responseMimeType: "application/json" }
        })
      }
    )

    const data = await response.json()
    if (!response.ok) {
      throw new Error(data.error?.message || "Gemini API request failed.")
    }

    let rawText = data.candidates?.[0]?.content?.parts?.[0]?.text ?? "{}"
    rawText = rawText.replace(/```json/gi, "").replace(/```/g, "").trim()

    return JSON.parse(rawText)
  }

  const handleSendMessage = (e?: React.FormEvent) => {
    if (e) e.preventDefault()
    if (!message.trim()) return

    const userText = message.trim()
    setMessage("")
    setShowAppsMenu(false)

    setChatHistory((prev) => [
      ...prev,
      { id: Date.now().toString(), sender: "user", text: userText }
    ])

    const isSummarizeIntent = /summarise|summarize/i.test(userText)

    if (isSummarizeIntent) {
      requestThreadOptions()
    } else {
      setChatHistory((prev) => [
        ...prev,
        {
          id: (Date.now() + 1).toString(),
          sender: "ai",
          text: "I can help summarize your inbox or process requests. Try clicking 'Summarise' below."
        }
      ])
    }
  }

  const requestThreadOptions = () => {
    setSummarizing(true)
    chrome.identity.getAuthToken({ interactive: true }, async (token) => {
      if (chrome.runtime.lastError || !token) {
        setChatHistory((prev) => [
          ...prev,
          { id: Date.now().toString(), sender: "ai", text: "Auth error. Please sign in again." }
        ])
        setSummarizing(false)
        return
      }

      try {
        const threads = await fetchRecentThreads(token)
        if (threads.length === 0) {
          setChatHistory((prev) => [
            ...prev,
            { id: Date.now().toString(), sender: "ai", text: "No email threads found in your inbox." }
          ])
        } else {
          setChatHistory((prev) => [
            ...prev,
            {
              id: Date.now().toString(),
              sender: "ai",
              text: "Which email thread would you like me to summarize?",
              threadsList: threads
            }
          ])
        }
      } catch (err: any) {
        setChatHistory((prev) => [
          ...prev,
          { id: Date.now().toString(), sender: "ai", text: `Error fetching emails: ${err.message}` }
        ])
      } finally {
        setSummarizing(false)
      }
    })
  }

  const handleQuickSummarize = () => {
    setChatHistory((prev) => [
      ...prev,
      { id: Date.now().toString(), sender: "user", text: "Summarise my emails" }
    ])
    requestThreadOptions()
  }

  const handleSelectThread = (threadId: string, snippet: string) => {
    setChatHistory((prev) => [
      ...prev,
      { id: Date.now().toString(), sender: "user", text: `Summarize: "${snippet.slice(0, 45)}..."` }
    ])
    setSummarizing(true)

    chrome.identity.getAuthToken({ interactive: true }, async (token) => {
      if (!token) return
      try {
        const messages = await fetchThreadMessagesById(token, threadId)
        const evidence = buildEvidenceSnapshot(messages)
        const result = await generateSummary(evidence)

        setChatHistory((prev) => [
          ...prev,
          { id: Date.now().toString(), sender: "ai", summary: result }
        ])
      } catch (err: any) {
        setChatHistory((prev) => [
          ...prev,
          { id: Date.now().toString(), sender: "ai", text: `Failed to summarize: ${err.message}` }
        ])
      } finally {
        setSummarizing(false)
      }
    })
  }

  const handleStartNewChat = () => {
    if (chatHistory.length > 0) {
      const newSession: ChatSession = {
        id: Date.now().toString(),
        title: chatHistory[0]?.text?.slice(0, 30) || "Summary Chat",
        history: chatHistory,
        createdAt: new Date()
      }
      setSavedSessions((prev) => [newSession, ...prev])
    }
    setChatHistory([])
    setShowHistoryDrawer(false)
    setShowAppsMenu(false)
  }

  const handleLoadSession = (session: ChatSession) => {
    setChatHistory(session.history)
    setShowHistoryDrawer(false)
  }

  const googleAppsList = [
    {
      name: "Google Calendar",
      desc: "Schedule & events",
      icon: (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
          <path d="M19 4H5C3.89 4 3 4.9 3 6V20C3 21.1 3.89 22 5 22H19C20.1 22 21 21.1 21 20V6C21 4.9 20.1 4 19 4Z" fill="#4285F4"/>
          <path d="M19 4H5C3.89 4 3 4.9 3 6V9H21V6C21 4.9 20.1 4 19 4Z" fill="#EA4335"/>
          <path d="M3 9H21V20C21 21.1 20.1 22 19 22H5C3.89 22 3 21.1 3 20V9Z" fill="#FFFFFF"/>
          <text x="12" y="18" fill="#4285F4" fontSize="9" fontWeight="bold" textAnchor="middle">31</text>
        </svg>
      )
    },
    {
      name: "Gmail",
      desc: "Emails & messages",
      icon: (
        <svg width="20" height="20" viewBox="0 0 24 24">
          <path fill="#EA4335" d="M20 18h-2V9.25L12 13 6 9.25V18H4V6h1.75l6.25 4 6.25-4H20v12z"/>
        </svg>
      )
    },
    {
      name: "Google Drive",
      desc: "Cloud storage",
      icon: (
        <svg width="20" height="20" viewBox="0 0 24 24">
          <path fill="#FFBA00" d="M7.71 3.5L1.14 14.86l3.43 5.95 6.57-11.36z"/>
          <path fill="#00AC47" d="M16.29 3.5H7.71l6.57 11.36h8.58z"/>
          <path fill="#0066DA" d="M4.57 20.81h17.14l-3.43-5.95H7.71z"/>
        </svg>
      )
    },
    {
      name: "Google Docs",
      desc: "Documents & notes",
      icon: (
        <svg width="20" height="20" viewBox="0 0 24 24">
          <path fill="#4285F4" d="M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/>
        </svg>
      )
    }
  ]

  return (
    <div className="container" style={{ position: "relative", display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden", backgroundColor: themeStyles.bg, color: themeStyles.text, transition: "background-color 0.2s, color 0.2s" }}>
      
      {/* Header bar section */}
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", borderBottom: `1px solid ${themeStyles.border}`, backgroundColor: themeStyles.headerBg, zIndex: 1 }}>
        {signedIn ? (
          <button
            onClick={() => setShowHistoryDrawer(!showHistoryDrawer)}
            title="Chat History"
            aria-label="Toggle chat history"
            style={{ backgroundColor: "transparent", border: "none", padding: "4px", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", borderRadius: "6px" }}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={themeStyles.text} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
              <line x1="9" y1="3" x2="9" y2="21" />
            </svg>
          </button>
        ) : (
          <div style={{ width: 20 }} />
        )}

        <span style={{ fontSize: "14px", fontWeight: 600, color: themeStyles.accent }}>Threadly</span>

        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button
            onClick={toggleTheme}
            aria-label="Toggle theme"
            title={isDark ? "Switch to Light Mode" : "Switch to Dark Mode"}
            style={{ width: 36, height: 20, borderRadius: 10, backgroundColor: isDark ? "#333" : "#e0e0e0", border: `1px solid ${themeStyles.border}`, padding: 2, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: isDark ? "flex-end" : "flex-start", transition: "all 0.2s", outline: "none" }}
          >
            <div style={{ width: 14, height: 14, borderRadius: "50%", backgroundColor: isDark ? themeStyles.accent : "#ffffff", boxShadow: "0 1px 3px rgba(0,0,0,0.3)" }} />
          </button>
        </div>
      </header>

      {/* History Drawer */}
      {signedIn && (
        <div
          style={{
            position: "absolute",
            top: 45,
            left: 0,
            width: "50%",
            bottom: 0,
            backgroundColor: themeStyles.drawerBg,
            zIndex: 50,
            padding: 16,
            overflowY: "auto",
            borderRight: `1px solid ${themeStyles.border}`,
            boxShadow: "4px 0 12px rgba(0,0,0,0.1)",
            transform: showHistoryDrawer ? "translateX(0)" : "translateX(-100%)",
            transition: "transform 0.3s ease-in-out"
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
            <h3 style={{ margin: 0, fontSize: 14, color: themeStyles.textHeading }}>History</h3>
            <button onClick={() => setShowHistoryDrawer(false)} style={{ backgroundColor: "transparent", border: "none", color: themeStyles.textMuted, fontSize: 16, cursor: "pointer" }}>✕</button>
          </div>

          <button onClick={handleStartNewChat} style={{ width: "100%", padding: "8px", borderRadius: 8, border: `1px solid ${themeStyles.border}`, backgroundColor: themeStyles.cardBg, color: themeStyles.textHeading, cursor: "pointer", marginBottom: 16, textAlign: "left", fontSize: 12 }}>
            + New Chat
          </button>

          {savedSessions.length === 0 ? (
            <p style={{ color: themeStyles.textMuted, fontSize: 12 }}>No chat history.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {savedSessions.map((session) => (
                <div key={session.id} onClick={() => handleLoadSession(session)} style={{ padding: "8px 10px", borderRadius: 6, backgroundColor: themeStyles.cardBg, border: `1px solid ${themeStyles.border}`, cursor: "pointer" }}>
                  <div style={{ fontSize: 12, color: themeStyles.textHeading, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{session.title}</div>
                  <div style={{ fontSize: 10, color: themeStyles.textMuted, marginTop: 2 }}>
                    {new Date(session.createdAt).toLocaleDateString()}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Auth Unauthenticated Screen */}
      {!signedIn && (
        <main className="content" style={{ display: "flex", flexDirection: "column", justifyContent: "center", flex: 1 }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", padding: "40px 20px", gap: 4 }}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>📥✨</div>
            <h2 style={{ margin: 0, fontSize: 22, color: themeStyles.textHeading }}>Sign in to Threadly</h2>
            <p style={{ color: themeStyles.textMuted, margin: "12px 0", fontSize: 14 }}>Get AI assistance with your emails and tasks.</p>
            <button
              onClick={signIn}
              disabled={loading}
              style={{ width: "100%", padding: "14px", borderRadius: "10px", border: "none", backgroundColor: themeStyles.accent, color: "#fff", fontWeight: 600, fontSize: 16, cursor: "pointer" }}
            >
              {loading ? "Signing in..." : "Sign in with Google"}
            </button>
            {error && <p style={{ color: "#f87171", fontSize: 13, marginTop: 10 }}>{error}</p>}
          </div>
        </main>
      )}

      {/* Main Chat / Artifact View */}
      {signedIn && (
        <div style={{ flex: 1, overflowY: "auto", padding: "16px 20px", display: "flex", flexDirection: "column", gap: 12 }}>
          {chatHistory.length === 0 && (
            <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <h3 style={{ margin: 0, fontSize: 20, fontWeight: 600, color: themeStyles.textHeading }}>
                Welcome to Threadly ✨
              </h3>
            </div>
          )}

          {chatHistory.map((item) => (
            <div
              key={item.id}
              style={{
                alignSelf: item.sender === "user" ? "flex-end" : "flex-start",
                maxWidth: "85%",
                backgroundColor: item.sender === "user" ? themeStyles.accent : themeStyles.aiBubbleBg,
                color: item.sender === "user" ? "#ffffff" : themeStyles.text,
                padding: "10px 14px",
                borderRadius: 14,
                border: item.sender === "ai" ? `1px solid ${themeStyles.border}` : "none",
                fontSize: 14
              }}
            >
              {item.text && <p style={{ margin: 0 }}>{item.text}</p>}

              {item.threadsList && (
                <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 10 }}>
                  {item.threadsList.map((t, idx) => (
                    <button
                      key={t.id}
                      onClick={() => handleSelectThread(t.id, t.snippet)}
                      style={{ textAlign: "left", padding: "8px 10px", borderRadius: 8, border: `1px solid ${themeStyles.border}`, backgroundColor: themeStyles.cardBg, cursor: "pointer", fontSize: 12, color: themeStyles.text }}
                    >
                      <strong style={{ color: themeStyles.accent }}>Email #{idx + 1}:</strong> {t.snippet.slice(0, 80)}...
                    </button>
                  ))}
                </div>
              )}

              {item.summary && (
                <div>
                  <h4 style={{ marginTop: 0, marginBottom: 6, color: themeStyles.accent }}>Summary Overview</h4>
                  <p style={{ margin: 0, fontSize: 13, color: themeStyles.textMuted }}>{item.summary.overview}</p>

                  {item.summary.decisions.length > 0 && (
                    <>
                      <h5 style={{ margin: "8px 0 2px", color: themeStyles.textHeading }}>Decisions</h5>
                      <ul style={{ fontSize: 12, paddingLeft: 16, margin: 0, color: themeStyles.textMuted }}>
                        {item.summary.decisions.map((d, i) => (
                          <li key={i}>{d.text} <em>({d.status})</em></li>
                        ))}
                      </ul>
                    </>
                  )}

                  {item.summary.actions.length > 0 && (
                    <>
                      <h5 style={{ margin: "8px 0 2px", color: themeStyles.textHeading }}>Actions</h5>
                      <ul style={{ fontSize: 12, paddingLeft: 16, margin: 0, color: themeStyles.textMuted }}>
                        {item.summary.actions.map((a, i) => (
                          <li key={i}>{a.text} {a.owner && `— ${a.owner}`}</li>
                        ))}
                      </ul>
                    </>
                  )}
                </div>
              )}
            </div>
          ))}

          {summarizing && (
            <div style={{ alignSelf: "flex-start", color: themeStyles.textMuted, fontSize: 13, fontStyle: "italic" }}>
              Processing request...
            </div>
          )}
        </div>
      )}

      {/* Input area & prompt shortcut section */}
      {signedIn && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, margin: "0 16px 16px", position: "relative" }}>
          
          {chatHistory.length === 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, paddingLeft: 4 }}>
              <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: themeStyles.textHeading }}>
                What's the plan?
              </h2>
              <button
                onClick={handleQuickSummarize}
                style={{
                  alignSelf: "flex-start",
                  padding: "6px 14px",
                  borderRadius: 16,
                  border: `1px solid ${themeStyles.border}`,
                  backgroundColor: themeStyles.cardBg,
                  color: themeStyles.textHeading,
                  fontSize: 13,
                  fontWeight: 600,
                  cursor: "pointer",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  boxShadow: "0 1px 3px rgba(0,0,0,0.05)"
                }}
              >
                <span style={{ color: themeStyles.accent }}>📄</span> Summarise
              </button>
            </div>
          )}

          <form
            onSubmit={handleSendMessage}
            style={{ display: "flex", alignItems: "center", gap: 6, padding: "8px 12px", backgroundColor: themeStyles.inputBg, border: `1px solid ${themeStyles.border}`, borderRadius: 20, position: "relative" }}
          >
            {/* Wrapper to anchor the menu directly over the plus button */}
            <div style={{ position: "relative", display: "inline-block" }}>
              {/* Clean Vector SVG Plus Icon */}
              <button
                type="button"
                onClick={() => setShowAppsMenu((prev) => !prev)}
                title="Google Apps menu"
                aria-label="Google Apps menu"
                style={{
                  flexShrink: 0,
                  width: 32,
                  height: 32,
                  borderRadius: "50%",
                  border: "none",
                  backgroundColor: showAppsMenu ? themeStyles.accent : themeStyles.buttonBg,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  padding: 0,
                  transition: "background-color 0.2s"
                }}
              >
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke={showAppsMenu ? "#ffffff" : themeStyles.textHeading}
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  style={{
                    transform: showAppsMenu ? "rotate(45deg)" : "rotate(0deg)",
                    transition: "transform 0.2s ease-in-out"
                  }}
                >
                  <line x1="12" y1="5" x2="12" y2="19" />
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
              </button>

              {/* Popup menu positioned directly above the plus button */}
              {showAppsMenu && (
                <div
                  style={{
                    position: "absolute",
                    bottom: "calc(100% + 12px)",
                    left: 0,
                    width: 220,
                    borderRadius: 16,
                    backgroundColor: themeStyles.cardBg,
                    border: `1px solid ${themeStyles.border}`,
                    boxShadow: "0 8px 24px rgba(0,0,0,0.15)",
                    padding: "8px 0",
                    zIndex: 100
                  }}
                >
                  <div style={{ padding: "6px 16px 8px 16px", fontSize: 11, fontWeight: 600, color: themeStyles.textMuted, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                    Google Apps
                  </div>
                  
                  {googleAppsList.map((app) => (
                    <div
                      key={app.name}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 12,
                        padding: "8px 16px",
                        cursor: "default",
                        userSelect: "none",
                        transition: "background-color 0.15s"
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = themeStyles.menuHoverBg)}
                      onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "transparent")}
                    >
                      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 24, height: 24 }}>
                        {app.icon}
                      </div>
                      <div style={{ display: "flex", flexDirection: "column" }}>
                        <span style={{ fontSize: 13, fontWeight: 500, color: themeStyles.textHeading }}>{app.name}</span>
                        <span style={{ fontSize: 11, color: themeStyles.textMuted }}>{app.desc}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <input
              type="text"
              placeholder={transcribing ? "Transcribing..." : "Type 'summarise' or ask a question..."}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              disabled={transcribing}
              style={{ flex: 1, padding: "6px 4px", border: "none", outline: "none", fontSize: "14px", backgroundColor: "transparent", color: themeStyles.text }}
            />

            <button
              type="button"
              onClick={toggleMic}
              title={recording ? "Stop Recording" : "Record voice message"}
              aria-label="Toggle voice recording"
              style={{ flexShrink: 0, width: 34, height: 34, borderRadius: "50%", border: "none", backgroundColor: recording ? "#ea4335" : themeStyles.buttonBg, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", padding: 0 }}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={recording ? "#ffffff" : themeStyles.textMuted} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="22" />
              </svg>
            </button>

            {message.trim() && (
              <button
                type="submit"
                aria-label="Send message"
                style={{ width: 34, height: 34, borderRadius: "50%", border: "none", backgroundColor: themeStyles.accent, color: "#ffffff", fontSize: 16, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}
              >
                ↑
              </button>
            )}
          </form>
        </div>
      )}
    </div>
  )
}

export default SidePanel
