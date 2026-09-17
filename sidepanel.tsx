import { useState, useRef } from "react"
import "./style.css"

// API configuration constants
const ELEVENLABS_API_KEY = process.env.PLASMO_PUBLIC_ELEVENLABS_API_KEY
const GEMINI_API_KEY = process.env.PLASMO_PUBLIC_GEMINI_API_KEY

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

interface DraftReply {
  to: string
  subject: string
  body: string
  status: "draft" | "sending" | "sent" | "failed"
  threadId?: string
  inReplyTo?: string
  errorMessage?: string
}

interface ChatMessage {
  id: string
  sender: "user" | "ai"
  text?: string
  threadsList?: ThreadOption[]
  summary?: SummaryOutput
  draft?: DraftReply
  taskId?: string
}

interface ChatSession {
  id: string
  title: string
  history: ChatMessage[]
  createdAt: Date
}

// Helper to validate Gmail API's strict 16-character hex thread ID requirement
const isValidHexThreadId = (id: string | null | undefined): boolean => {
  return !!id && /^[0-9a-fA-F]{16}$/.test(id)
}

// Helper function to extract a clean email address from "Name <email@domain.com>" or text
const extractEmailAddress = (str: string): string => {
  if (!str) return ""
  const match = str.match(/<([^>]+)>/) || str.match(/([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})/)
  return match ? match[1].trim() : ""
}

interface DomEmailData {
  evidence: EvidenceItem[]
  authorEmail: string | null
  legacyThreadId: string | null
  subject: string | null
  activeMessageId: string | null
}

// Helper function to query the active Gmail DOM via the content script
const fetchActiveEmailFromDOM = async (): Promise<DomEmailData | null> => {
  return new Promise((resolve) => {
    chrome.tabs.query({ active: true, lastFocusedWindow: true }, (tabs) => {
      const activeTab = tabs[0]

      if (!activeTab?.id || !activeTab.url?.includes("mail.google.com")) {
        return resolve(null)
      }

      chrome.tabs.sendMessage(
        activeTab.id,
        { action: "GET_OPEN_EMAIL_TEXT" },
        (response) => {
          if (chrome.runtime.lastError || !response?.evidence) {
            return resolve(null)
          }
          resolve({
            evidence: response.evidence,
            authorEmail: response.authorEmail || null,
            legacyThreadId: response.legacyThreadId || null,
            subject: response.subject || null,
            activeMessageId: response.activeMessageId || null
          })
        }
      )
    })
  })
}

// Extracts the Gmail thread ID from the active tab's URL hash.
const getActiveThreadIdFromUrl = (): Promise<string | null> => {
  return new Promise((resolve) => {
    chrome.tabs.query({ active: true, lastFocusedWindow: true }, (tabs) => {
      const url = tabs[0]?.url || ""
      const hashPart = url.split("#")[1] || ""
      const segments = hashPart.split("/").filter(Boolean)
      const lastSegment = segments[segments.length - 1] || ""
      resolve(isValidHexThreadId(lastSegment) ? lastSegment : null)
    })
  })
}

// Gets the message's Message-ID and From headers, plus the canonical Gmail API thread id.
const fetchSpecificMessageHeaderId = async (
  token: string,
  threadId: string,
  targetMessageId?: string | null
): Promise<{ inReplyTo: string | null; canonicalThreadId: string | null; fromAddress: string | null }> => {
  const res = await fetch(
    `https://gmail.googleapis.com/gmail/v1/users/me/threads/${threadId}?format=metadata&metadataHeaders=Message-ID&metadataHeaders=From`,
    { headers: { Authorization: `Bearer ${token}` } }
  )
  const data = await res.json()

  if (!res.ok) {
    throw new Error(data?.error?.message || `Gmail rejected thread id "${threadId}"`)
  }

  const messages = data.messages || []
  
  // Find the targeted active message or fallback to the last message in thread
  let targetMsg = messages[messages.length - 1]
  if (targetMessageId) {
    const found = messages.find((m: any) => m.id === targetMessageId)
    if (found) targetMsg = found
  }

  const headers = targetMsg?.payload?.headers || []
  const messageIdHeader = headers.find((h: any) => h.name.toLowerCase() === "message-id")
  const fromHeader = headers.find((h: any) => h.name.toLowerCase() === "from")

  return {
    inReplyTo: messageIdHeader?.value || null,
    canonicalThreadId: data.id || null,
    fromAddress: fromHeader?.value ? extractEmailAddress(fromHeader.value) : null
  }
}

// Sanitizes header values to prevent "Invalid header" errors
const sanitizeHeaderValue = (value: string) => value.replace(/[\r\n]+/g, " ").trim()

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

  // Recording & Voice Bubble States
  const [recording, setRecording] = useState<boolean>(false)
  const [transcribing, setTranscribing] = useState<boolean>(false)
  const [speaking, setSpeaking] = useState<boolean>(false)
  const [audioScale, setAudioScale] = useState<number>(1)
  const [voiceStatus, setVoiceStatus] = useState<string>("Listening...")

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const audioContextRef = useRef<AudioContext | null>(null)
  const animFrameRef = useRef<number | null>(null)

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

  const speakText = (text: string, onComplete?: () => void) => {
    if (!("speechSynthesis" in window)) {
      if (onComplete) onComplete()
      return
    }

    window.speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.rate = 1.0

    setSpeaking(true)
    setVoiceStatus("Replying...")

    const pulseInterval = setInterval(() => {
      setAudioScale(1 + Math.random() * 0.35)
    }, 120)

    utterance.onend = () => {
      clearInterval(pulseInterval)
      setAudioScale(1)
      setSpeaking(false)
      if (onComplete) onComplete()
    }

    utterance.onerror = () => {
      clearInterval(pulseInterval)
      setAudioScale(1)
      setSpeaking(false)
      if (onComplete) onComplete()
    }

    window.speechSynthesis.speak(utterance)
  }

  const transcribeAudio = async (audioBlob: Blob) => {
    setTranscribing(true)
    setVoiceStatus("Thinking...")

    const formData = new FormData()
    formData.append("file", audioBlob, "recording.webm")
    formData.append("model_id", "scribe_v2")

    let transcribedText = ""

    try {
      const res = await fetch("https://api.elevenlabs.io/v1/speech-to-text", {
        method: "POST",
        headers: { "xi-api-key": ELEVENLABS_API_KEY },
        body: formData
      })
      const data = await res.json()
      if (data.text) {
        transcribedText = data.text
      }
    } catch (err) {
      console.error("Transcription failed: ", err)
    } finally {
      setTranscribing(false)
    }

    if (transcribedText.trim()) {
      handleUserInstruction(transcribedText, true)
    } else {
      setVoiceStatus("Could not hear audio.")
      setTimeout(() => setVoiceStatus("Listening..."), 2000)
    }
  }

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      
      const audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)()
      const analyser = audioCtx.createAnalyser()
      const source = audioCtx.createMediaStreamSource(stream)
      analyser.fftSize = 64
      source.connect(analyser)
      audioContextRef.current = audioCtx

      const dataArray = new Uint8Array(analyser.frequencyBinCount)

      const updateAudioLevel = () => {
        analyser.getByteFrequencyData(dataArray)
        const avg = dataArray.reduce((acc, val) => acc + val, 0) / dataArray.length
        const normalizedScale = 1 + (avg / 255) * 0.8
        setAudioScale(normalizedScale)
        animFrameRef.current = requestAnimationFrame(updateAudioLevel)
      }

      updateAudioLevel()

      const recorder = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" })
      chunksRef.current = []
      recorder.ondataavailable = (e) => chunksRef.current.push(e.data)
      recorder.onstop = async () => {
        if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current)
        if (audioContextRef.current) audioContextRef.current.close()
        setAudioScale(1)
        stream.getTracks().forEach((t) => t.stop())
        
        const audioBlob = new Blob(chunksRef.current, { type: recorder.mimeType })
        await transcribeAudio(audioBlob)
      }

      recorder.start()
      mediaRecorderRef.current = recorder
      setRecording(true)
      setVoiceStatus("Listening...")
    } catch (err) {
      console.error("Mic permission error:", err)
      alert("Microphone access was blocked or dismissed.")
    }
  }

  const stopRecording = () => {
    mediaRecorderRef.current?.stop()
    setRecording(false)
  }

  const toggleMic = () => {
    if (speaking) {
      window.speechSynthesis.cancel()
      setSpeaking(false)
      return
    }

    if (recording) {
      stopRecording()
    } else {
      startRecording()
    }
  }

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
    headers.find((h) => h.name.toLowerCase() === name.toLowerCase())?.value || ""

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

  const generateDraftReply = async (
    evidence: EvidenceItem[],
    userInstruction: string,
    authoritativeEmail: string | null = null
  ): Promise<{ to: string; subject: string; body: string }> => {
    let fallbackEmail = authoritativeEmail || ""
    if (!fallbackEmail) {
      for (const item of [...evidence].reverse()) {
        const extracted = extractEmailAddress(item.author) || extractEmailAddress(item.snippet)
        if (extracted) {
          fallbackEmail = extracted
          break
        }
      }
    }

    const prompt = `You are an AI assistant drafting an email response on behalf of the user.
Context Email Thread:
${evidence.map((e) => `From: ${e.author}\nDate: ${e.date}\nContent: ${e.snippet}`).join("\n---\n")}

User Directive: "${userInstruction}"

Draft a polite, context-aware reply body executing the user directive.
IMPORTANT FOR "to": Scan the email content/headers above to extract ONLY the clean email address (e.g. 'user@domain.com'). Do NOT output thread labels like "Message 1". If no clean email is found, return "${fallbackEmail}".

Respond ONLY with valid JSON matching this schema:
{
  "to": "string (Valid recipient email address)",
  "subject": "string (Re: subject line based on thread content)",
  "body": "string (the plain text email body content)"
}`

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
      throw new Error(data.error?.message || "Failed to generate reply draft.")
    }

    let rawText = data.candidates?.[0]?.content?.parts?.[0]?.text ?? "{}"
    rawText = rawText.replace(/```json/gi, "").replace(/```/g, "").trim()

    const parsed = JSON.parse(rawText)
    const extractedTo = extractEmailAddress(parsed.to)

    return {
      to: authoritativeEmail || extractedTo || fallbackEmail,
      subject: parsed.subject || "Re: Your message",
      body: parsed.body || ""
    }
  }

  // Generates a brand-new email (not a reply) from a recipient address and a topic/instruction.
  const generateComposeEmail = async (
    toEmail: string,
    topic: string
  ): Promise<{ subject: string; body: string }> => {
    const prompt = `You are an AI assistant composing a brand new email on behalf of the user. This is NOT a reply to any existing thread — there is no prior context to reference.

Recipient: ${toEmail}
Topic / instructions from the user: "${topic}"

Write a clear, polite, professional email body covering the topic. Also write a concise, relevant subject line. Do NOT prefix the subject with "Re:" since this is a new email, not a reply.

Respond ONLY with valid JSON matching this schema:
{
  "subject": "string",
  "body": "string (the plain text email body content)"
}`

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
      throw new Error(data.error?.message || "Failed to generate composed email.")
    }

    let rawText = data.candidates?.[0]?.content?.parts?.[0]?.text ?? "{}"
    rawText = rawText.replace(/```json/gi, "").replace(/```/g, "").trim()

    const parsed = JSON.parse(rawText)
    return {
      subject: parsed.subject || "New message",
      body: parsed.body || ""
    }
  }

  // Handle a "compose a new email" instruction, e.g.
  // "Compose an email to: jane@example.com, talking about the Q3 roadmap."
  // Produces a standalone draft with no threadId/inReplyTo — sending it creates a brand
  // new email rather than replying in any existing thread.
  const handleComposeEmail = async (instruction: string, reciteVoice: boolean = false) => {
    setSummarizing(true)

    try {
      // Look for an address right after "to" (with or without a colon) first, since that's
      // the expected phrasing; fall back to scanning the whole instruction for anything
      // email-shaped if that doesn't match.
      const toMatch = instruction.match(/\bto:?\s*([^\s,]+@[^\s,;]+)/i)
      const toEmail = extractEmailAddress(toMatch?.[1] || instruction)

      if (!toEmail) {
        const errorText =
          'I couldn\'t find a recipient address. Try something like: "Compose an email to: name@example.com, talking about the project update."'
        setChatHistory((prev) => [
          ...prev,
          { id: Date.now().toString(), sender: "ai", text: errorText }
        ])
        if (reciteVoice) speakText(errorText)
        setSummarizing(false)
        return
      }

      // The topic is everything after "about"/"talking about"/"regarding"; fall back to the
      // full instruction if none of those keywords are present.
      const topicMatch = instruction.match(/(?:talking about|about|regarding)\s*[:\-]?\s*(.+)$/i)
      const topic = (topicMatch?.[1] || instruction).trim()

      const generated = await generateComposeEmail(toEmail, topic)

      const draftMessage: ChatMessage = {
        id: Date.now().toString(),
        sender: "ai",
        text: "I've composed a new email for you. Review it and click send when ready:",
        draft: {
          to: toEmail,
          subject: generated.subject,
          body: generated.body,
          status: "draft"
          // no threadId / inReplyTo — this is intentionally a brand new email
        }
      }

      setChatHistory((prev) => [...prev, draftMessage])

      if (reciteVoice) {
        speakText("I composed a new email for you. Review it in the side panel.")
      }
    } catch (err: any) {
      setChatHistory((prev) => [
        ...prev,
        { id: Date.now().toString(), sender: "ai", text: `Compose error: ${err.message}` }
      ])
      if (reciteVoice) speakText(`Sorry, I couldn't compose that email. ${err.message}`)
    } finally {
      setSummarizing(false)
    }
  }

  // Handle drafting routine when requested
  const handleReplyDraft = async (instruction: string, reciteVoice: boolean = false) => {
    setSummarizing(true)

    try {
      const domData = await fetchActiveEmailFromDOM()

      if (!domData || domData.evidence.length === 0) {
        const errorText = "No open email tab detected. Open an email thread in Gmail to draft a reply."
        setChatHistory((prev) => [
          ...prev,
          { id: Date.now().toString(), sender: "ai", text: errorText }
        ])
        if (reciteVoice) speakText(errorText)
        setSummarizing(false)
        return
      }

      const scrapedThreadId = domData.legacyThreadId || (await getActiveThreadIdFromUrl())

      let inReplyTo: string | null = null
      let canonicalThreadId: string | null = null
      let apiFromAddress: string | null = null
      let threadResolved = false

      if (scrapedThreadId && isValidHexThreadId(scrapedThreadId)) {
        await new Promise<void>((resolve) => {
          chrome.identity.getAuthToken({ interactive: false }, async (token) => {
            if (token) {
              try {
                const result = await fetchSpecificMessageHeaderId(token, scrapedThreadId, domData.activeMessageId)
                inReplyTo = result.inReplyTo
                apiFromAddress = result.fromAddress
                if (result.canonicalThreadId && isValidHexThreadId(result.canonicalThreadId)) {
                  canonicalThreadId = result.canonicalThreadId
                  threadResolved = true
                }
              } catch (err) {
                console.error("Thread id could not be validated against the Gmail API:", err)
              }
            }
            resolve()
          })
        })
      }

      const authoritativeEmail = domData.authorEmail || apiFromAddress
      const generatedDraft = await generateDraftReply(domData.evidence, instruction, authoritativeEmail)

      const realSubject = domData.subject
        ? domData.subject.trim().toLowerCase().startsWith("re:")
          ? domData.subject.trim()
          : `Re: ${domData.subject.trim()}`
        : null

      const validThreadId = isValidHexThreadId(canonicalThreadId) ? canonicalThreadId! : undefined

      const draftMessage: ChatMessage = {
        id: Date.now().toString(),
        sender: "ai",
        text: validThreadId
          ? "I've drafted a reply for you. It'll be added to the same email thread — review and click send when ready:"
          : "I've drafted a reply, but I couldn't confirm which thread this belongs to, so sending it will create a new email rather than replying in-thread. Review and click send when ready:",
        draft: {
          to: generatedDraft.to,
          subject: realSubject || generatedDraft.subject || "Re: Your message",
          body: generatedDraft.body,
          status: "draft",
          threadId: validThreadId,
          inReplyTo: inReplyTo || undefined
        }
      }

      setChatHistory((prev) => [...prev, draftMessage])

      if (reciteVoice) {
        speakText("I generated a draft reply for you. Review it in the side panel.")
      }
    } catch (err: any) {
      setChatHistory((prev) => [
        ...prev,
        { id: Date.now().toString(), sender: "ai", text: `Draft error: ${err.message}` }
      ])
    } finally {
      setSummarizing(false)
    }
  }

  // Sends the email using Gmail REST API with proper reply headers and thread ID
  const executeSendEmail = async (messageId: string, draft: DraftReply) => {
    setChatHistory((prev) =>
      prev.map((msg) =>
        msg.id === messageId && msg.draft
          ? { ...msg, draft: { ...msg.draft, status: "sending" } }
          : msg
      )
    )

    chrome.identity.getAuthToken({ interactive: true }, async (token) => {
      if (chrome.runtime.lastError || !token) {
        setChatHistory((prev) =>
          prev.map((msg) =>
            msg.id === messageId && msg.draft
              ? { ...msg, draft: { ...msg.draft, status: "failed", errorMessage: "Authentication failed" } }
              : msg
          )
        )
        return
      }

      try {
        const to = extractEmailAddress(sanitizeHeaderValue(draft.to))
        const subject = sanitizeHeaderValue(draft.subject)

        if (!to || !/[^\s@]+@[^\s@]+\.[^\s@]+/.test(to)) {
          throw new Error(`"${draft.to}" is not a valid email address. Edit the To field and try again.`)
        }

        const headerLines = [
          `To: ${to}`,
          `Subject: ${subject}`,
          'Content-Type: text/plain; charset="UTF-8"',
          'MIME-Version: 1.0'
        ]

        if (draft.inReplyTo) {
          const cleanInReplyTo = sanitizeHeaderValue(draft.inReplyTo)
          headerLines.push(`In-Reply-To: ${cleanInReplyTo}`)
          headerLines.push(`References: ${cleanInReplyTo}`)
        }

        const rawEmail = [...headerLines, '', draft.body].join('\r\n')

        const encodedMessage = btoa(unescape(encodeURIComponent(rawEmail)))
          .replace(/\+/g, '-')
          .replace(/\//g, '_')
          .replace(/=+$/, '')

        const requestBody: { raw: string; threadId?: string } = { raw: encodedMessage }

        // Strictly check thread ID format before appending to payload
        if (isValidHexThreadId(draft.threadId)) {
          requestBody.threadId = draft.threadId
        }

        const res = await fetch("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", {
          method: "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json"
          },
          body: JSON.stringify(requestBody)
        })

        if (!res.ok) {
          const errBody = await res.json().catch(() => null)
          throw new Error(errBody?.error?.message || "API dispatch failed")
        }

        setChatHistory((prev) =>
          prev.map((msg) =>
            msg.id === messageId && msg.draft
              ? { ...msg, draft: { ...msg.draft, status: "sent" } }
              : msg
          )
        )
      } catch (err) {
        console.error("Email send failed:", err)
        const errMessage = err instanceof Error ? err.message : "Unknown error"
        setChatHistory((prev) =>
          prev.map((msg) =>
            msg.id === messageId && msg.draft
              ? { ...msg, draft: { ...msg.draft, status: "failed", errorMessage: errMessage } }
              : msg
          )
        )
      }
    })
  }

  const handleSmartSummarize = async (reciteVoice: boolean = false) => {
    setSummarizing(true)

    try {
      const domData = await fetchActiveEmailFromDOM()

      if (domData && domData.evidence.length > 0) {
        const result = await generateSummary(domData.evidence)

        setChatHistory((prev) => [
          ...prev,
          { id: Date.now().toString(), sender: "ai", summary: result }
        ])

        if (reciteVoice) {
          speakText(`Here is the summary: ${result.overview}`)
        }
        setSummarizing(false)
        return
      }

      chrome.identity.getAuthToken({ interactive: true }, async (token) => {
        if (chrome.runtime.lastError || !token) {
          const errText = "Auth error. Please sign in to access recent threads."
          setChatHistory((prev) => [
            ...prev,
            { id: Date.now().toString(), sender: "ai", text: errText }
          ])
          if (reciteVoice) speakText(errText)
          setSummarizing(false)
          return
        }

        const threads = await fetchRecentThreads(token)
        if (threads.length === 0) {
          const emptyText = "No open email detected on screen and no recent threads found."
          setChatHistory((prev) => [
            ...prev,
            { id: Date.now().toString(), sender: "ai", text: emptyText }
          ])
          if (reciteVoice) speakText(emptyText)
        } else {
          const introText = "No open email detected on screen. Select a thread to summarize:"
          setChatHistory((prev) => [
            ...prev,
            {
              id: Date.now().toString(),
              sender: "ai",
              text: introText,
              threadsList: threads
            }
          ])

          if (reciteVoice) {
            const fullScript = `${introText} ` + threads
              .map((t, i) => `Option ${i + 1}: ${t.snippet}`)
              .join(". ")
            speakText(fullScript)
          }
        }
        setSummarizing(false)
      })
    } catch (err: any) {
      const errorMsg = `Error processing email: ${err.message}`
      setChatHistory((prev) => [
        ...prev,
        { id: Date.now().toString(), sender: "ai", text: errorMsg }
      ])
      if (reciteVoice) speakText(errorMsg)
      setSummarizing(false)
    }
  }

  const handleUserInstruction = (input: string, reciteVoice: boolean = false) => {
    const userText = input.trim()
    if (!userText) return

    setChatHistory((prev) => [
      ...prev,
      { id: Date.now().toString(), sender: "user", text: userText }
    ])

    const isComposeIntent = /^compose\b/i.test(userText) || /\bcompose\s+(an\s+)?email\b/i.test(userText)
    const isReplyIntent = /^reply\b/i.test(userText)
    const isSummarizeIntent = /summarise|summarize/i.test(userText)

    if (isComposeIntent) {
      handleComposeEmail(userText, reciteVoice)
    } else if (isReplyIntent) {
      handleReplyDraft(userText, reciteVoice)
    } else if (isSummarizeIntent) {
      handleSmartSummarize(reciteVoice)
    } else {
      const resp = "I can help summarize emails, draft replies, or compose new emails. Try 'Summarise', 'reply saying yes', or 'Compose an email to: name@example.com, talking about the project update.'"
      setChatHistory((prev) => [
        ...prev,
        { id: (Date.now() + 1).toString(), sender: "ai", text: resp }
      ])
      if (reciteVoice) speakText(resp)
    }
  }

  const handleSendMessage = (e?: React.FormEvent) => {
    if (e) e.preventDefault()
    if (!message.trim()) return

    const userText = message.trim()
    setMessage("")
    setShowAppsMenu(false)
    handleUserInstruction(userText, false)
  }

  const handleQuickSummarize = () => {
    handleUserInstruction("Summarise my open email", false)
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

  const isVoiceActive = recording || transcribing || speaking

  return (
    <div className="container" style={{ position: "relative", display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden", backgroundColor: themeStyles.bg, color: themeStyles.text, transition: "background-color 0.2s, color 0.2s" }}>
      
      {/* Voice Bubble Overlay */}
      {isVoiceActive && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            backgroundColor: "rgba(0, 0, 0, 0.75)",
            backdropFilter: "blur(6px)",
            zIndex: 200,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            animation: "fadeIn 0.2s ease-out"
          }}
        >
          <div style={{ position: "relative", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <div
              style={{
                position: "absolute",
                width: 140,
                height: 140,
                borderRadius: "50%",
                background: `radial-gradient(circle, ${themeStyles.accent} 0%, rgba(232, 89, 12, 0) 70%)`,
                transform: `scale(${audioScale * 1.3})`,
                opacity: 0.6,
                transition: "transform 0.08s ease-out"
              }}
            />
            
            <div
              style={{
                width: 100,
                height: 100,
                borderRadius: "50%",
                background: speaking
                  ? "linear-gradient(135deg, #4285F4, #9b51e0)"
                  : `linear-gradient(135deg, ${themeStyles.accent}, #ff8c00)`,
                transform: `scale(${audioScale})`,
                boxShadow: "0 8px 32px rgba(0, 0, 0, 0.4)",
                transition: "transform 0.08s ease-out, background 0.3s ease",
                display: "flex",
                alignItems: "center",
                justifyContent: "center"
              }}
            >
              <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#ffffff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                {speaking ? (
                  <>
                    <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                    <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" />
                  </>
                ) : (
                  <>
                    <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
                    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                    <line x1="12" y1="19" x2="12" y2="22" />
                  </>
                )}
              </svg>
            </div>
          </div>

          <p style={{ marginTop: 28, color: "#ffffff", fontSize: 16, fontWeight: 500 }}>
            {transcribing ? "Transcribing speech..." : voiceStatus}
          </p>

          <button
            onClick={toggleMic}
            style={{
              marginTop: 16,
              padding: "8px 20px",
              borderRadius: 20,
              border: "1px solid rgba(255, 255, 255, 0.3)",
              backgroundColor: "rgba(255, 255, 255, 0.1)",
              color: "#ffffff",
              fontSize: 13,
              fontWeight: 500,
              cursor: "pointer"
            }}
          >
            {speaking ? "Stop Response" : "Done Speaking"}
          </button>
        </div>
      )}

      {/* Header section */}
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

      {/* Unauthenticated Screen */}
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

      {/* Main Chat / Output View */}
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

              {/* Draft card preview with send functionality */}
              {item.draft && (
                <div style={{ marginTop: 10, padding: 10, backgroundColor: themeStyles.cardBg, borderRadius: 8, border: `1px solid ${themeStyles.border}` }}>
                  <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 8 }}>
                    <label style={{ fontSize: 11, color: themeStyles.textMuted }}>
                      To:
                      <input
                        type="text"
                        value={item.draft.to}
                        onChange={(e) => {
                          const updatedTo = e.target.value
                          setChatHistory((prev) =>
                            prev.map((msg) =>
                              msg.id === item.id && msg.draft
                                ? { ...msg, draft: { ...msg.draft, to: updatedTo } }
                                : msg
                            )
                          )
                        }}
                        disabled={item.draft.status !== "draft" && item.draft.status !== "failed"}
                        style={{ width: "100%", marginTop: 2, padding: 6, borderRadius: 4, border: `1px solid ${themeStyles.border}`, backgroundColor: themeStyles.inputBg, color: themeStyles.text, fontSize: 12, boxSizing: "border-box" }}
                      />
                    </label>
                    <label style={{ fontSize: 11, color: themeStyles.textMuted }}>
                      Subject:
                      <input
                        type="text"
                        value={item.draft.subject}
                        onChange={(e) => {
                          const updatedSubject = e.target.value
                          setChatHistory((prev) =>
                            prev.map((msg) =>
                              msg.id === item.id && msg.draft
                                ? { ...msg, draft: { ...msg.draft, subject: updatedSubject } }
                                : msg
                            )
                          )
                        }}
                        disabled={item.draft.status !== "draft" && item.draft.status !== "failed"}
                        style={{ width: "100%", marginTop: 2, padding: 6, borderRadius: 4, border: `1px solid ${themeStyles.border}`, backgroundColor: themeStyles.inputBg, color: themeStyles.text, fontSize: 12, boxSizing: "border-box" }}
                      />
                    </label>
                  </div>
                  <textarea
                    value={item.draft.body}
                    onChange={(e) => {
                      const updatedBody = e.target.value
                      setChatHistory((prev) =>
                        prev.map((msg) =>
                          msg.id === item.id && msg.draft
                            ? { ...msg, draft: { ...msg.draft, body: updatedBody } }
                            : msg
                        )
                      )
                    }}
                    disabled={item.draft.status !== "draft" && item.draft.status !== "failed"}
                    style={{
                      width: "100%",
                      height: 100,
                      borderRadius: 6,
                      border: `1px solid ${themeStyles.border}`,
                      backgroundColor: themeStyles.inputBg,
                      color: themeStyles.text,
                      padding: 8,
                      fontSize: 12,
                      resize: "vertical",
                      boxSizing: "border-box"
                    }}
                  />
                  <div style={{ marginTop: 8, display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 8 }}>
                    {item.draft.status === "sent" ? (
                      <span style={{ fontSize: 12, color: "#10b981", fontWeight: 600 }}>✓ Sent successfully</span>
                    ) : item.draft.status === "sending" ? (
                      <span style={{ fontSize: 12, color: themeStyles.textMuted }}>Sending...</span>
                    ) : item.draft.status === "failed" ? (
                      <>
                        {item.draft.errorMessage && (
                          <span style={{ fontSize: 11, color: "#ef4444", marginRight: "auto" }}>
                            {item.draft.errorMessage}
                          </span>
                        )}
                        <button
                          onClick={() => executeSendEmail(item.id, item.draft!)}
                          style={{ padding: "6px 12px", borderRadius: 6, backgroundColor: "#ef4444", color: "#fff", border: "none", cursor: "pointer", fontSize: 12 }}
                        >
                          Retry Send
                        </button>
                      </>
                    ) : (
                      <button
                        onClick={() => executeSendEmail(item.id, item.draft!)}
                        style={{ padding: "6px 14px", borderRadius: 6, backgroundColor: themeStyles.accent, color: "#fff", border: "none", cursor: "pointer", fontSize: 12, fontWeight: 600 }}
                      >
                        Send Email
                      </button>
                    )}
                  </div>
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

      {/* Input controls */}
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
            <div style={{ position: "relative", display: "inline-block" }}>
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
              placeholder={transcribing ? "Transcribing..." : "Ask anything..."}
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