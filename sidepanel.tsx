import { useState, useRef, useEffect } from "react"
import "./style.css"
import { icons } from "~assets/icons"

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
  summaryMeta?: { subject: string | null; messageCount: number }
  summaryLoading?: boolean
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

const sanitizeHeaderValue = (value: string) => value.replace(/[\r\n]+/g, " ").trim()

function SidePanel() {
  const [theme, setTheme] = useState<"dark" | "light">("light")
  const [signedIn, setSignedIn] = useState<boolean>(false)
  const [userName, setUserName] = useState<string>("")
  const [userEmail, setUserEmail] = useState<string>("")
  const [userAvatar, setUserAvatar] = useState<string>("")
  const [loading, setLoading] = useState<boolean>(false)

  const [message, setMessage] = useState<string>("")
  const [chatHistory, setChatHistory] = useState<ChatMessage[]>([])

  const [savedSessions, setSavedSessions] = useState<ChatSession[]>([])
  const [showHistoryDrawer, setShowHistoryDrawer] = useState<boolean>(false)
  const [showOptionsMenu, setShowOptionsMenu] = useState<boolean>(false)

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

  const [emailOpen, setEmailOpen] = useState<boolean>(false)

  const isDark = theme === "dark"
  const themeStyles = {
    bg: isDark ? "#121212" : "#FFFFFF",
    headerBg: isDark ? "#121212" : "#FFFFFF",
    drawerBg: isDark ? "#181818" : "#F4F3EF",
    cardBg: isDark ? "#1e1e1e" : "#FFFFFF",
    aiBubbleBg: isDark ? "#1e1e1e" : "#F4F3EF",
    inputBg: isDark ? "#121212" : "#FFFFFF",
    inputBorder: isDark ? "#333333" : "#d0d0d0",
    border: isDark ? "#2a2a2a" : "#E8E6E1",
    text: isDark ? "#e0e0e0" : "#000000",
    textMuted: isDark ? "#aaaaaa" : "#808080",
    textHeading: isDark ? "#ffffff" : "#000000",
    buttonBg: isDark ? "#2a2a2a" : "#F2F1ED",
    accent: isDark ? "#ffffff" : "#000000",
    popoverBg: isDark ? "#222222" : "#FFFFFF",
    popoverBorder: isDark ? "#333333" : "#E0E0E0"
  }

  const checkEmailOpen = async () => {
    const domData = await fetchActiveEmailFromDOM()
    setEmailOpen(!!domData && domData.evidence.length > 0)
  }

  useEffect(() => {
    if (!signedIn) return
    checkEmailOpen()
    const interval = setInterval(checkEmailOpen, 2000)
    return () => clearInterval(interval)
  }, [signedIn])

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
        setUserEmail(profile.email || "")
        setUserName(profile.name || profile.email || "")
        if (profile.picture) setUserAvatar(profile.picture)
        setSignedIn(true)
      } catch (err) {
        console.error("Userinfo fetch error:", err)
        setError("Could not retrieve user profile.")
      } finally {
        setLoading(false)
      }
    })
  }

  const signOut = () => {
    chrome.identity.getAuthToken({ interactive: false }, (token) => {
      if (token) {
        fetch(`https://accounts.google.com/o/oauth2/revoke?token=${token}`).catch(() => {})
        chrome.identity.removeCachedAuthToken({ token }, () => {})
      }
      setSignedIn(false)
      setUserName("")
      setUserEmail("")
      setUserAvatar("")
      setChatHistory([])
      setShowOptionsMenu(false)
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

  const handleComposeEmail = async (instruction: string, reciteVoice: boolean = false) => {
    setSummarizing(true)

    try {
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
        const messageId = Date.now().toString()

        setChatHistory((prev) => [
          ...prev,
          {
            id: messageId,
            sender: "ai",
            summaryMeta: { subject: domData.subject, messageCount: domData.evidence.length },
            summaryLoading: true
          }
        ])

        try {
          const result = await generateSummary(domData.evidence)
          setChatHistory((prev) =>
            prev.map((msg) =>
              msg.id === messageId ? { ...msg, summary: result, summaryLoading: false } : msg
            )
          )
          if (reciteVoice) speakText(`Here is the summary: ${result.overview}`)
        } catch (err: any) {
          setChatHistory((prev) =>
            prev.map((msg) =>
              msg.id === messageId
                ? { ...msg, summaryLoading: false, text: `Error generating summary: ${err.message}` }
                : msg
            )
          )
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
    const isReplyIntent = /^reply\b/i.test(userText) || /draft a reply/i.test(userText)
    const isSummarizeIntent = /summarise|summarize/i.test(userText)

    if (isComposeIntent) {
      handleComposeEmail(userText, reciteVoice)
    } else if (isReplyIntent) {
      handleReplyDraft(userText, reciteVoice)
    } else if (isSummarizeIntent) {
      handleSmartSummarize(reciteVoice)
    } else {
      const resp = "I can help summarize emails, draft replies, or search your mail. Try asking 'Summarise this thread'."
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
    handleUserInstruction(userText, false)
  }

  const handleDismissSummary = (id: string) => {
    setChatHistory((prev) => prev.filter((msg) => msg.id !== id))
  }

  // "Draft reply" link inside the summary card — jumps straight into the reply flow.
  const handleDraftReplyFromSummary = () => {
    handleReplyDraft("Draft a reply to this thread.")
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
  }

  const handleLoadSession = (session: ChatSession) => {
    setChatHistory(session.history)
    setShowHistoryDrawer(false)
  }

  const isVoiceActive = recording || transcribing || speaking

  const getInitials = (name: string) => {
    if (!name) return "U"
    const parts = name.trim().split(" ")
    if (parts.length >= 2) return `${parts[0][0]}${parts[1][0]}`.toUpperCase()
    return name.slice(0, 2).toUpperCase()
  }

  return (
    <div
      style={{
        position: "relative",
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        overflow: "hidden",
        backgroundColor: themeStyles.bg,
        color: themeStyles.text,
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
      }}
    >

      {/* ----------------- ADD HEADER HERE ----------------- */}
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "10px 14px",
        borderBottom: `1px solid ${themeStyles.border}`,
        backgroundColor: themeStyles.cardBg,
        position: "relative",
        zIndex:400
      }}
    >
      {/* Left Title */}
      <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: themeStyles.textHeading }}>
        Threadly
      </h1>

      {/* Right Actions (New Chat & Sidepanel History Toggle) */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 8 }}>
        <button
          onClick={handleStartNewChat}
          aria-label="New Chat"
          title="New Chat"
          style={{
            background: "none",
            border: "none",
            cursor: "pointer",
            padding: 4,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: themeStyles.textHeading,
            borderRadius: 6
          }}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
        </button>

        <button
          onClick={() => setShowHistoryDrawer(!showHistoryDrawer)}
          aria-label="Toggle Sidepanel"
          title="Sidepanel"
          style={{
            background: "none",
            border: "none",
            cursor: "pointer",
            padding: 4,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: themeStyles.textHeading,
            borderRadius: 6
          }}
        >
          <img src={icons.sidebar} width={16} height={16} alt="" style={{ filter: recording ? "invert(1)" : "none" }} />
        </button>
      </div>
    </div>
    {/* ---------------------------------------------------- */}
    
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
                width: 120,
                height: 120,
                borderRadius: "50%",
                background: isDark
                  ? "radial-gradient(circle, rgba(255, 255, 255, 0.15) 0%, rgba(255, 255, 255, 0) 70%)"
                  : "radial-gradient(circle, rgba(0, 0, 0, 0.15) 0%, rgba(0, 0, 0, 0) 70%)",
                transform: `scale(${audioScale * 1.3})`,
                opacity: 0.6,
                transition: "transform 0.08s ease-out"
              }}
            />
            <div
              style={{
                width: 84,
                height: 84,
                borderRadius: "50%",
                background: isDark ? "#ffffff" : "#000000",
                transform: `scale(${audioScale})`,
                boxShadow: "0 8px 32px rgba(0, 0, 0, 0.4)",
                transition: "transform 0.08s ease-out, background 0.3s ease",
                display: "flex",
                alignItems: "center",
                justifyContent: "center"
              }}
            >
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke={isDark ? "#000000" : "#ffffff"} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
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

          <p style={{ marginTop: 24, color: "#ffffff", fontSize: 14, fontWeight: 500 }}>
            {transcribing ? "Transcribing speech..." : voiceStatus}
          </p>

          <button
            onClick={toggleMic}
            style={{
              marginTop: 14,
              padding: "6px 16px",
              borderRadius: 20,
              border: "1px solid rgba(255, 255, 255, 0.3)",
              backgroundColor: "rgba(255, 255, 255, 0.1)",
              color: "#ffffff",
              fontSize: 12,
              fontWeight: 500,
              cursor: "pointer"
            }}
          >
            {speaking ? "Stop Response" : "Done Speaking"}
          </button>
        </div>
      )}

      {/* Sidebar (full screen) */}
{signedIn && (
  <div
    style={{
      position: "fixed",
      inset: 0,
      backgroundColor: themeStyles.bg,
      zIndex: 300,
      display: "flex",
      flexDirection: "column",
      paddingTop: 48,
      transform: showHistoryDrawer ? "translateX(0)" : "translateX(-100%)",
      transition: "transform 0.25s ease-in-out"
    }}
  >
    {/* New Chat button — full width, top */}
    <div style={{ padding: 14 }}>
      <button
        onClick={handleStartNewChat}
        style={{
          width: "100%",
          padding: "12px 14px",
          borderRadius: 10,
          border: `1px solid ${themeStyles.border}`,
          backgroundColor: themeStyles.cardBg,
          color: themeStyles.text,
          cursor: "pointer",
          fontSize: 12,
          fontWeight: 600,
          display: "flex",
          alignItems: "center",
          justifyContent: "flex-start",
          gap: 8
        }}
      >
        <img src={icons.plus} width={16} height={16} alt="" style={{ filter: isDark ? "invert(1)" : "none" }} />
        New Chat
      </button>
    </div>

    {/* Chat History list */}
    <div style={{ flex: 1, overflowY: "auto", padding: "0 14px" }}>
      <h3 style={{ margin: "0 0 8px 4px", fontSize: 12, color: themeStyles.textMuted, textTransform: "uppercase", letterSpacing: "0.5px" }}>
        Chats
      </h3>

      {savedSessions.length === 0 ? (
        <p style={{ color: themeStyles.textMuted, fontSize: 12, padding: "0 4px" }}>No chat history.</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 20 }}>
          {savedSessions.map((session) => (
            <div
              key={session.id}
              onClick={() => handleLoadSession(session)}
              style={{
                display: "flex",
               alignItems: "center",
               justifyContent: "space-between",
               padding: "8px 4px",
               cursor: "pointer"
              }}
            >
              <div style={{ fontSize: 12, color: themeStyles.textHeading, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {session.title}
              </div>
              <div style={{ fontSize: 11, color: themeStyles.text, marginTop: 2 }}>
                {new Date(session.createdAt).toLocaleDateString()}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Tools used — quick shortcuts to re-trigger common actions */}
      <h3 style={{ margin: "0 0 8px 4px", fontSize: 12, color: themeStyles.textMuted, textTransform: "uppercase", letterSpacing: "0.5px" }}>
        Tools
      </h3>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <button
          onClick={() => {
            setShowHistoryDrawer(false)
            handleSmartSummarize
          }}
          style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "10px 12px", borderRadius: 8,
            border: "none", backgroundColor: "transparent",
            cursor: "pointer", fontSize: 12, color: themeStyles.text, textAlign: "left"
          }}
        >
          <img src={icons.meeting} width={16} height={16} alt="" style={{ filter: isDark ? "invert(1)" : "none" }} />
          Google Calendar
        </button>

        <button
          onClick={() => {
            setShowHistoryDrawer(false)
            handleUserInstruction("Draft a reply to this thread")
          }}
          style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "10px 12px", borderRadius: 8,
            border: "none", backgroundColor: "transparent",
            cursor: "pointer", fontSize: 12, color: themeStyles.text, textAlign: "left"
          }}
        >
          <img src={icons.draft} width={16} height={16} alt="" style={{ filter: isDark ? "invert(1)" : "none" }} />
          Google Drive
        </button>

        <button
          onClick={() => {
            setShowHistoryDrawer(false)
            setMessage("Compose an email to: ")
          }}
          style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "10px 12px", borderRadius: 8,
           border: "none", backgroundColor: "transparent",
            cursor: "pointer", fontSize: 12, color: themeStyles.text, textAlign: "left"
          }}
        >
          <img src={icons.hr} width={16} height={16} alt="" style={{ filter: isDark ? "invert(1)" : "none" }} />
          Contacts
        </button>
      </div>
    </div>

    {/* Back to chat — bottom left */}
    <div style={{ padding: 14, borderTop: `1px solid ${themeStyles.border}` }}>
      <button
        onClick={() => setShowHistoryDrawer(false)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          background: "none",
          border: "none",
          cursor: "pointer",
          color: themeStyles.textHeading,
          fontSize: 13,
          fontWeight: 500,
          padding: "6px 4px"
        }}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="15 18 9 12 15 6" />
        </svg>
        Back to chat
      </button>
    </div>
  </div>
)}

      {/* Unauthenticated View */}
      {!signedIn ? (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", height: "100%" }}>
          {/* Scrollable Area */}
          <div style={{ flex: 1, overflowY: "auto", padding: "20px 16px 12px 16px", display: "flex", flexDirection: "column", justifyContent: "flex-end" }}>
            <div style={{ display: "flex", flexDirection: "column" }}>
              <h1 style={{ margin: "0 0 8px 0", fontSize: 24, fontWeight: 700, color: themeStyles.textHeading, letterSpacing: "-0.3px" }}>
                Welcome to Threadly
              </h1>
              
              <p style={{ margin: "0 0 20px 0", fontSize: 14, lineHeight: "1.4", color: themeStyles.text }}>
                Sign in with Google to search your mail, summarise threads, and draft quick replies.
              </p>
            </div>
          </div>

          {/* Bottom Card & Controls Section */}
          <div style={{ padding: "0 14px 10px 14px", display: "flex", flexDirection: "column", gap: 10, position: "relative" }}>
            
            {/* Options Menu Popover for Guest */}
            {showOptionsMenu && (
              <div
                style={{
                  position: "absolute",
                  bottom: 50,
                  right: 14,
                  width: 160,
                  backgroundColor: themeStyles.popoverBg,
                  border: `1px solid ${themeStyles.popoverBorder}`,
                  borderRadius: 8,
                  boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
                  padding: "6px 0",
                  zIndex: 100,
                  display: "flex",
                  flexDirection: "column"
                }}
              >
                <button
                  onClick={() => setTheme(isDark ? "light" : "dark")}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "8px 12px",
                    background: "none",
                    border: "none",
                    color: themeStyles.textHeading,
                    fontSize: 12,
                    cursor: "pointer",
                    textAlign: "left"
                  }}
                >
                  <span>Dark Mode</span>
                  <span style={{ fontSize: 11, fontWeight: 600, color: themeStyles.textMuted }}>
                    {isDark ? "ON" : "OFF"}
                  </span>
                </button>
              </div>
            )}

            {/* Input Card Container for Sign In */}
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                border: `1px solid ${themeStyles.inputBorder}`,
                borderRadius: 14,
                backgroundColor: themeStyles.cardBg,
                padding: "12px 14px",
                boxShadow: "0 1px 2px rgba(0,0,0,0.02)"
              }}
            >
              <div style={{ fontSize: 14, color: themeStyles.textMuted, marginBottom: 12 }}>
                Authentication required
              </div>

              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span style={{ fontSize: 12, color: themeStyles.textMuted, fontWeight: 400 }}>
                  Google Account
                </span>

                <button
                  onClick={signIn}
                  disabled={loading}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "6px 12px",
                    borderRadius: 18,
                    border: `1px solid ${themeStyles.inputBorder}`,
                    backgroundColor: themeStyles.cardBg,
                    color: themeStyles.textHeading,
                    fontSize: 12,
                    fontWeight: 600,
                    cursor: loading ? "default" : "pointer",
                    boxShadow: "0 1px 2px rgba(0,0,0,0.05)"
                  }}
                >
                  <svg width="14" height="14" viewBox="0 0 18 18">
                    <path fill="#4285F4" d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.259h2.908c1.702-1.567 2.684-3.874 2.684-6.617z"/>
                    <path fill="#34A853" d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z"/>
                    <path fill="#FBBC05" d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z"/>
                    <path fill="#EA4335" d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z"/>
                  </svg>
                  {loading ? "Signing in..." : "Sign in"}
                </button>
              </div>
            </div>

            {error && <p style={{ color: "#ea4335", fontSize: 11, margin: "2px 0 0 4px" }}>{error}</p>}

            {/* Profile Footer Bar (Guest Mode) */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                paddingTop: 6,
                borderTop: `1px solid ${themeStyles.border}`
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <div
                  style={{
                    width: 30,
                    height: 30,
                    borderRadius: "50%",
                    backgroundColor: isDark ? "#333333" : "#e8e3d9",
                    color: themeStyles.textHeading,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 12,
                    fontWeight: 600
                  }}
                >
                  G
                </div>
                <span style={{ fontSize: 13, fontWeight: 500, color: themeStyles.textHeading }}>
                  Guest
                </span>
              </div>

              <button
                onClick={() => setShowOptionsMenu(!showOptionsMenu)}
                title="Options"
                style={{
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  padding: 3,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: themeStyles.textHeading
                }}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                  <circle cx="12" cy="5" r="2"/>
                  <circle cx="12" cy="12" r="2"/>
                  <circle cx="12" cy="19" r="2"/>
                </svg>
              </button>
            </div>
          </div>
        </div>
      ) : (
        /* Authenticated Main Container */
        <div style={{ flex: 1, display: "flex", flexDirection: "column", height: "100%" }}>
          
          {/* Scrollable Content Body */}
          <div style={{ flex: 1, overflowY: "auto", padding: "20px 16px 12px 16px", display: "flex", flexDirection: "column", justifyContent: chatHistory.length === 0 ? "flex-end" : "flex-start" }}>
            
            {/* Initial Screen state pushed to bottom when chat history is empty */}
            {chatHistory.length === 0 ? (
              <div style={{ display: "flex", flexDirection: "column", marginTop: "auto" }}>
                <h1 style={{ margin: "0 0 8px 0", fontSize: 24, fontWeight: 700, color: themeStyles.textHeading, letterSpacing: "-0.3px" }}>
                  Welcome to Threadly
                </h1>
                
                <p style={{ margin: "0 0 20px 0", fontSize: 14, lineHeight: "1.4", color: themeStyles.text }}>
                  Ask anything and Threadly will search your mail. Open a thread to summarise or draft a reply.
                </p>

                {/* Prompt List Options */}
                <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                  
                  {/* Summarise this thread */}
                  <button
                    onClick={() => handleSmartSummarize()}
                    disabled={!emailOpen}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      background: "none",
                      border: "none",
                      padding: 0,
                      cursor: emailOpen ? "pointer" : "default",
                      opacity: emailOpen ? 1 : 0.45,
                      textAlign: "left"
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                      <img src={icons.summarise} width={16} height={16} alt="" style={{ filter: recording ? "invert(1)" : "none" }} />
                      <span style={{ fontSize: 14, color: themeStyles.textHeading, fontWeight: 400 }}>Summarise this thread</span>
                    </div>
                    {!emailOpen && <span style={{ fontSize: 12, color: themeStyles.textMuted }}>Open an email</span>}
                  </button>

                  {/* Draft a reply to this thread */}
                  <button
                    onClick={() => handleUserInstruction("Draft a reply to this thread")}
                    disabled={!emailOpen}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      background: "none",
                      border: "none",
                      padding: 0,
                      cursor: emailOpen ? "pointer" : "default",
                      opacity: emailOpen ? 1 : 0.45,
                      textAlign: "left"
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                      <img src={icons.draft} width={16} height={16} alt="" style={{ filter: recording ? "invert(1)" : "none" }} />
                      <span style={{ fontSize: 14, color: themeStyles.textHeading, fontWeight: 400 }}>Draft a reply to this thread</span>
                    </div>
                    {!emailOpen && <span style={{ fontSize: 12, color: themeStyles.textMuted }}>Open an email</span>}
                  </button>

                </div>
              </div>
            ) : (
              /* Chat Message Feed */
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {chatHistory.map((item) => (
                  <div
                    key={item.id}
                    style={{
                      alignSelf: item.sender === "user" ? "flex-end" : "flex-start",
                      maxWidth: "100%",
                      backgroundColor: item.sender === "user" ? themeStyles.accent : themeStyles.bg,
                      color: item.sender === "user" ? (isDark ? "#000000" : "#ffffff") : themeStyles.text,
                      padding: "8px 12px",
                      borderRadius: 12,
                      border: item.sender === "ai" ? `1px solid ${themeStyles.border}` : "none",
                      fontSize: 13
                    }}
                  >
                    {item.text && <p style={{ margin: 0, lineHeight: 1.4 }}>{item.text}</p>}

                    {item.threadsList && (
                      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 8 }}>
                        {item.threadsList.map((t, idx) => (
                          <button
                            key={t.id}
                            onClick={() => handleSelectThread(t.id, t.snippet)}
                            style={{ textAlign: "left", padding: "6px 8px", borderRadius: 6, border: `1px solid ${themeStyles.border}`, backgroundColor: themeStyles.cardBg, cursor: "pointer", fontSize: 11, color: themeStyles.text }}
                          >
                            <strong style={{ color: themeStyles.textHeading }}>Email #{idx + 1}:</strong> {t.snippet.slice(0, 80)}...
                          </button>
                        ))}
                      </div>
                    )}

                                       {(item.summary || item.summaryMeta) && (
                      <div>
                        {item.summaryMeta?.subject && (
                          <>
                            <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: 1, color: themeStyles.textMuted, marginBottom: 6 }}>
                              THREAD CONTEXT
                            </div>
                            <div
                              style={{
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "space-between",
                                gap: 8,
                                padding: "8px 10px",
                                borderRadius: 8,
                                border: `1px solid ${themeStyles.border}`,
                                backgroundColor: themeStyles.cardBg,
                                marginBottom: 10
                              }}
                            >
                              <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                                <span style={{ fontSize: 13 }}>🧵</span>
                                <span style={{ fontSize: 12, color: themeStyles.textHeading, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                  {item.summaryMeta.subject}
                                </span>
                              </div>
                              <button
                                onClick={() => handleDismissSummary(item.id)}
                                aria-label="Dismiss"
                                style={{ background: "transparent", border: "none", color: themeStyles.textMuted, fontSize: 13, cursor: "pointer", padding: 0, flexShrink: 0 }}
                              >
                                ✕
                              </button>
                            </div>
                          </>
                        )}

                        <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: 1, color: themeStyles.textMuted, marginBottom: 6 }}>
                          SUMMARY
                        </div>

                        {item.summaryLoading && (
                          <p style={{ margin: 0, fontSize: 12, color: themeStyles.textMuted, fontStyle: "italic" }}>
                          Summarising...
                          </p>
                        )}

                        {!item.summaryLoading && item.summary && (
                        <ul style={{ fontSize: 12, paddingLeft: 16, margin: 0, color: themeStyles.text, display: "flex", flexDirection: "column", gap: 6 }}>
                          {item.summary.overview && <li>{item.summary.overview}</li>}
                          {item.summary.decisions.map((d, i) => (
                            <li key={`d-${i}`}>{d.text}{d.status === "proposed" ? " (proposed)" : ""}</li>
                          ))}
                          {item.summary.actions.map((a, i) => (
                            <li key={`a-${i}`}>{a.text}{a.owner ? ` — ${a.owner}` : ""}{a.due ? ` (due ${a.due})` : ""}</li>
                          ))}
                          {item.summary.unresolvedQuestions.map((q, i) => (
                            <li key={`q-${i}`}>{q.text}</li>
                          ))}
                        </ul>
                        )}

                        {!item.summaryLoading && item.summaryMeta && item.summary && (
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 12 }}>
                            <span style={{ fontSize: 11, color: themeStyles.textMuted }}>
                              From {item.summaryMeta.messageCount} message{item.summaryMeta.messageCount === 1 ? "" : "s"}
                            </span>
                            <button
                              onClick={handleDraftReplyFromSummary}
                              style={{ background: "transparent", border: "none", color: themeStyles.textHeading, fontSize: 12, fontWeight: 700, cursor: "pointer", padding: 0 }}
                            >
                              Draft reply
                            </button>
                          </div>
                        )}
                      </div>
                    )}

                    {item.draft && (
                      <div style={{ marginTop: 8, padding: 8, backgroundColor: themeStyles.cardBg, borderRadius: 6, border: `1px solid ${themeStyles.border}` }}>
                        <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 6 }}>
                          <label style={{ fontSize: 10, color: themeStyles.textMuted }}>
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
                              style={{ width: "100%", marginTop: 2, padding: 4, borderRadius: 4, border: `1px solid ${themeStyles.border}`, backgroundColor: themeStyles.inputBg, color: themeStyles.text, fontSize: 11, boxSizing: "border-box" }}
                            />
                          </label>
                          <label style={{ fontSize: 10, color: themeStyles.textMuted }}>
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
                              style={{ width: "100%", marginTop: 2, padding: 4, borderRadius: 4, border: `1px solid ${themeStyles.border}`, backgroundColor: themeStyles.inputBg, color: themeStyles.text, fontSize: 11, boxSizing: "border-box" }}
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
                            height: 80,
                            borderRadius: 4,
                            border: `1px solid ${themeStyles.border}`,
                            backgroundColor: themeStyles.inputBg,
                            color: themeStyles.text,
                            padding: 6,
                            fontSize: 11,
                            resize: "vertical",
                            boxSizing: "border-box"
                          }}
                        />
                        <div style={{ marginTop: 6, display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 6 }}>
                          {item.draft.status === "sent" ? (
                            <span style={{ fontSize: 11, color: "#10b981", fontWeight: 600 }}>✓ Sent successfully</span>
                          ) : item.draft.status === "sending" ? (
                            <span style={{ fontSize: 11, color: themeStyles.textMuted }}>Sending...</span>
                          ) : item.draft.status === "failed" ? (
                            <>
                              {item.draft.errorMessage && (
                                <span style={{ fontSize: 10, color: "#ef4444", marginRight: "auto" }}>
                                  {item.draft.errorMessage}
                                </span>
                              )}
                              <button
                                onClick={() => executeSendEmail(item.id, item.draft!)}
                                style={{ padding: "4px 10px", borderRadius: 4, backgroundColor: "#ef4444", color: "#fff", border: "none", cursor: "pointer", fontSize: 11 }}
                              >
                                Retry Send
                              </button>
                            </>
                          ) : (
                            <button
                              onClick={() => executeSendEmail(item.id, item.draft!)}
                              style={{ padding: "5px 12px", borderRadius: 4, backgroundColor: themeStyles.accent, color: isDark ? "#000" : "#fff", border: "none", cursor: "pointer", fontSize: 11, fontWeight: 600 }}
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
                  <div style={{ alignSelf: "flex-start", color: themeStyles.textMuted, fontSize: 12, fontStyle: "italic" }}>
                    Processing request...
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Bottom Card & Controls Section */}
          <div style={{ padding: "0 14px 10px 14px", display: "flex", flexDirection: "column", gap: 10, position: "relative" }}>
            
            {/* Options Menu Popover */}
            {showOptionsMenu && (
              <div
                style={{
                  position: "absolute",
                  bottom: 50,
                  right: 14,
                  width: 160,
                  backgroundColor: themeStyles.popoverBg,
                  border: `1px solid ${themeStyles.popoverBorder}`,
                  borderRadius: 8,
                  boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
                  padding: "6px 0",
                  zIndex: 100,
                  display: "flex",
                  flexDirection: "column"
                }}
              >
                <button
                  onClick={() => setShowHistoryDrawer(true)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    padding: "8px 12px",
                    background: "none",
                    border: "none",
                    color: themeStyles.textHeading,
                    fontSize: 12,
                    cursor: "pointer",
                    textAlign: "left"
                  }}
                >
                  View History
                </button>

                <button
                  onClick={() => setTheme(isDark ? "light" : "dark")}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "8px 12px",
                    background: "none",
                    border: "none",
                    color: themeStyles.textHeading,
                    fontSize: 12,
                    cursor: "pointer",
                    textAlign: "left"
                  }}
                >
                  <span>Dark Mode</span>
                  <span style={{ fontSize: 11, fontWeight: 600, color: themeStyles.textMuted }}>
                    {isDark ? "ON" : "OFF"}
                  </span>
                </button>

                <div style={{ height: 1, backgroundColor: themeStyles.popoverBorder, margin: "4px 0" }} />

                <button
                  onClick={signOut}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    padding: "8px 12px",
                    background: "none",
                    border: "none",
                    color: "#ef4444",
                    fontSize: 12,
                    fontWeight: 500,
                    cursor: "pointer",
                    textAlign: "left"
                  }}
                >
                  Sign Out
                </button>
              </div>
            )}

            {/* Rounded Input Box Card */}
            <form
              onSubmit={handleSendMessage}
              style={{
                display: "flex",
                flexDirection: "column",
                border: `1px solid ${themeStyles.inputBorder}`,
                borderRadius: 14,
                backgroundColor: themeStyles.cardBg,
                padding: "12px 14px",
                boxShadow: "0 1px 2px rgba(0,0,0,0.02)"
              }}
            >
              <input
                type="text"
                placeholder="Ask anything across your mail"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                disabled={transcribing}
                style={{
                  border: "none",
                  outline: "none",
                  fontSize: 14,
                  color: themeStyles.textHeading,
                  width: "100%",
                  backgroundColor: "transparent",
                  marginBottom: 12
                }}
              />

              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span style={{ fontSize: 12, color: themeStyles.textMuted, fontWeight: 400 }}>
                  All mail
                </span>

                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <button
                    type="button"
                    onClick={toggleMic}
                    title="Voice search"
                    style={{
                      background: "none",
                      border: "none",
                      cursor: "pointer",
                      padding: 3,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      color: recording ? "#ea4335" : themeStyles.textHeading
                    }}
                  >
                    <img src={icons.voice} width={16} height={16} alt="" style={{ filter: recording ? "invert(1)" : "none" }} />
                  </button>

                  <button
                    type="submit"
                    disabled={!message.trim()}
                    style={{
                      width: 32,
                      height: 32,
                      borderRadius: "50%",
                      border: "none",
                      backgroundColor: themeStyles.accent,
                      color: isDark ? "#000000" : "#FFFFFF",
                      cursor: message.trim() ? "pointer" : "default",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      opacity: message.trim() ? 1 : 0.4,
                      transition: "opacity 0.15s, background-color 0.15s"
                    }}
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="12" y1="19" x2="12" y2="5"/>
                      <polyline points="5 12 12 5 19 12"/>
                    </svg>
                  </button>
                </div>
              </div>
            </form>

            {/* Profile Footer Bar */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                paddingTop: 6,
                borderTop: `1px solid ${themeStyles.border}`
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {userAvatar ? (
                  <img
                    src={userAvatar}
                    alt={userName}
                    style={{ width: 30, height: 30, borderRadius: "50%", objectFit: "cover" }}
                  />
                ) : (
                  <div
                    style={{
                      width: 30,
                      height: 30,
                      borderRadius: "50%",
                      backgroundColor: isDark ? "#333333" : "#e8e3d9",
                      color: themeStyles.textHeading,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 12,
                      fontWeight: 600
                    }}
                  >
                    {getInitials(userName || userEmail)}
                  </div>
                )}
                <span style={{ fontSize: 13, fontWeight: 500, color: themeStyles.textHeading }}>
                  {userName || userEmail || "User"}
                </span>
              </div>

              <button
                onClick={() => setShowOptionsMenu(!showOptionsMenu)}
                title="Options"
                style={{
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  padding: 3,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: themeStyles.textHeading
                }}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                  <circle cx="12" cy="5" r="2"/>
                  <circle cx="12" cy="12" r="2"/>
                  <circle cx="12" cy="19" r="2"/>
                </svg>
              </button>
            </div>

          </div>
        </div>
      )}
    </div>
  )
}

export default SidePanel