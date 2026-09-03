import { useState, useRef } from "react"
import "./style.css"

const ELEVENLABS_API_KEY="sk_476386bd65baf349698531a300fdbf63503a2a3d5fbea555"

const BACKEND_URL = "https://YOUR_BACKEND_DOMAIN"

function SidePanel() {
  const [signedIn, setSignedIn] = useState(false)
  const [email, setEmail] = useState("")
  const [loading, setLoading] = useState(false)

   // ===== CHANGED: added to hold our backend's JWT, not Google's token =====
  const [authToken, setAuthToken] = useState("")
  // ===== END CHANGED =====


  // State for the bottom chat input
  const [message, setMessage] = useState("")

  // State for voice recording

  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  // ===== CHANGED: signIn rewritten to go through backend, not getAuthToken =====
  const signIn = async () => {
    setLoading(true)
    try {
      // Step 1: ask our backend for the Google consent URL
      const urlRes = await fetch(`${BACKEND_URL}/v1/auth/google/url`)
      const { url: consentUrl } = await urlRes.json()

      // Step 2: launch the real OAuth flow through Chrome, using OUR backend's consent URL
      chrome.identity.launchWebAuthFlow(
        { url: consentUrl, interactive: true },
        async (redirectedTo) => {
          if (chrome.runtime.lastError || !redirectedTo) {
            console.error(chrome.runtime.lastError)
            setLoading(false)
            return
          }

          // Step 3: pull the ?code=... param out of the redirect
          const redirectUrl = new URL(redirectedTo)
          const code = redirectUrl.searchParams.get("code")
          if (!code) {
            console.error("No code returned from OAuth redirect")
            setLoading(false)
            return
          }

          // Step 4: exchange the code with OUR backend (backend talks to Google itself)
          const exchangeRes = await fetch(`${BACKEND_URL}/v1/auth/google/exchange`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code })
          })
          const data = await exchangeRes.json()

          // Step 5: store OUR backend's JWT + user info, not any Google token
          setAuthToken(data.token)
          setEmail(data.email)
          setSignedIn(true)
          setLoading(false)
        }
      )
    } catch (err) {
      console.error("Sign-in failed:", err)
      setLoading(false)
    }
  }
  // ===== END CHANGED =====

  const handleSendMessage = (e) => {
    e.preventDefault()
    if (!message.trim()) return
    console.log("Submitted message:", message)
    setMessage("")
  }

  const transcribeAudio = async (audioBlob : Blob) => {
    setTranscribing(true)
    const formData = new FormData()
    formData.append("file", audioBlob, "recording.webm")
    formData.append("model_id", "scribe_v2")
    try{
      const res = await fetch("https://api.elevenlabs.io/v1/speech-to-text", {
        method:"POST",
        headers: {"xi-api-key" : ELEVENLABS_API_KEY},
        body : formData
      })
      const data = await res.json()
      if (data.text){
        setMessage((prev) =>  (prev ? prev + " " + data.text : data.text))
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
      console.log("Recorded blob size:", audioBlob.size, "type:", audioBlob.type)
      await transcribeAudio(audioBlob)
    }
    recorder.start()
    mediaRecorderRef.current = recorder
    setRecording(true)
  } catch (err) {
    console.error("Mic permission error:", err)
    alert("Microphone access was blocked or dismissed. Please try again and click Allow when prompted.")
  }
}

  const stopRecording = () => {
    mediaRecorderRef.current?.stop()
    setRecording(false)
  }

  const toggleMic = () => {
    if (recording) {
      stopRecording()
    } else {
      startRecording()
    }
  }
  return (
    <div className="container" style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden" }}>

      {!signedIn && (
        <main
          className="content"
          style={{
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
            flex: 1
          }}
        >
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              textAlign: "center",
              padding: "40px 20px",
              gap: 4
            }}
          >
            <div style={{ fontSize: 48, marginBottom: 16 }}>📥✨</div>

            <h2 style={{ margin: 0, fontSize: 22 }}>Sign in to chat with Threadly</h2>

            <p style={{ color: "#666", margin: "12px 0", fontSize: 14 }}>
              Get help with your emails.
            </p>
            <p style={{ color: "#666", margin: "0 0 12px", fontSize: 14 }}>
              Sign in so our AI can:
            </p>

            <ul
              style={{
                textAlign: "left",
                color: "#444",
                fontSize: 14,
                margin: "8px 0 24px",
                paddingLeft: 20
              }}
            >
              <li style={{ marginBottom: 8 }}>Categorise Emails</li>
              <li style={{ marginBottom: 8 }}>Summarise multiple threads</li>
              <li style={{ marginBottom: 8 }}>Draft replies to emails</li>
            </ul>

            <button
              onClick={signIn}
              disabled={loading}
              style={{
                width: "100%",
                padding: "14px",
                borderRadius: "10px",
                border: "none",
                backgroundColor: "#e8590c",
                color: "#000",
                fontWeight: 600,
                fontSize: 16,
                cursor: "pointer"
              }}
            >
              {loading ? "Signing in..." : "Sign in"}
            </button>
          </div>
        </main>
      )}

      {/* Display a message above the chat box */}

       {signedIn && (
        <div
          style={{
            position: "absolute",
            top: "33%",
            left: 0,
            right: 0,
            transform: "translateY(-50%)",
            textAlign: "center",
            padding: "0 20px"
          }}
        >
          <h2 style={{ margin: 0, fontSize: 20, color: "#222" }}>
          Welcome to our LLM
          </h2>
        </div>
      )}


      {signedIn && (
        <div
          style={{
            position: "absolute",
            top: "66%",
            left: 0,
            right: 0,
            transform: "translateY(-50%)",
            textAlign: "center",
            padding: "0 20px"
          }}
        >
          <h3 style={{ margin: 0, fontSize: 16, color: "#333" }}>
            Your next insight is waiting ✨
          </h3>
          <p style={{ margin: "4px 0 0", fontSize: 13, color: "#888" }}>
            Ask me to summarize, draft, or find anything in your inbox.
          </p>
        </div>
      )}

      {/* Bottom Chat Input Section */}
      {signedIn && (
        <form onSubmit={handleSendMessage} style={{ display: "flex", gap: "8px", marginTop: "auto", paddingTop: "12px", alignItems: "center" }}>
          <button 
            type="button"
            onClick={toggleMic}
            title={recording ? "Stop Recording" : "Record voice message"}
            style={{
              flexShrink: 0,
               width: 40,
               height: 40,
               borderRadius: "50%",
               border: "none",
               backgroundColor: recording ? "#ea4335" : "#f1f3f4",
               cursor: "pointer",
               fontSize: 16
            }}
          >
            {recording ? "⏹" : "🎤"}
          </button>

          <input
            type="text"
            placeholder={transcribing ? "Transcribing..." : "Type a message..."}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            disabled={transcribing}
            style={{
              flex: 1,
              padding: "10px 14px",
              borderRadius: "20px",
              border: "1px solid #ccc",
              outline: "none",
              fontSize: "14px"
            }}
          />
          <button
            type="submit"
            style={{
              padding: "10px 16px",
              borderRadius: "20px",
              border: "none",
              backgroundColor: "#1a73e8",
              color: "#ffffff",
              fontWeight: "600",
              cursor: "pointer",
            }}
          >
            Send
          </button>
        </form>
      )}
    </div>
  )
}

export default SidePanel