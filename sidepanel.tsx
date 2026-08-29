import { useState, useRef } from "react"
import "./style.css"

const ELEVENLABS_API_KEY="sk_476386bd65baf349698531a300fdbf63503a2a3d5fbea555"

function SidePanel() {
  const [signedIn, setSignedIn] = useState(false)
  const [email, setEmail] = useState("")
  const [loading, setLoading] = useState(false)

  // State for the bottom chat input
  const [message, setMessage] = useState("")

  // State for voice recording

  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  const signIn = () => {
    setLoading(true)
    chrome.identity.getAuthToken({ interactive: true }, async (token) => {
      if (chrome.runtime.lastError || !token) {
        console.error(chrome.runtime.lastError)
        setLoading(false)
        return
      }

      const res = await fetch(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        { headers: { Authorization: `Bearer ${token}` } }
      )
      const profile = await res.json()

      setEmail(profile.email)
      setSignedIn(true)
      setLoading(false)
    })
  }

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
    <div className="container">
      <header className="header">
        <h1>Threadly</h1>
        <p>Your email assistant</p>
      </header>

      <main className="content">
        <h2>Welcome to Threadly</h2>

        {signedIn ? (
          <>
          <p>Signed in as {email}</p>
          </>
        ) : (
          <>
            <p>Your email assistant will appear here.</p>
            {/* Google-styled button wrapper */}
            <button className="gsi-material-button" onClick={signIn} disabled={loading}>
              <div className="gsi-material-button-state"></div>
              <div 
                className="gsi-material-button-content-wrapper" 
                style={{ display: "flex", flexDirection: "row", alignItems: "center", justifyContent: "center", gap: "10px" }}
              >
                <div className="gsi-material-button-icon" style={{ width: "20px", height: "20px" }}>
                  <svg version="1.1" xmlns="http://w3.org/2000/svg" viewBox="0 0 48 48" style={{ display: "block", width: "100%", height: "100%" }}>
                    <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"></path>
                    <path fill="#4285F4" d="M46.5 24c0-1.61-.15-3.16-.43-4.69H24v9.03h12.75c-.55 2.92-2.2 5.39-4.68 7.05v5.86h7.59c4.44-4.09 7-10.1 7-17.25z"></path>
                    <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"></path>
                    <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.59-5.86c-2.11 1.41-4.8 2.25-8.3 2.25-6.26 0-11.57-4.22-13.46-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"></path>
                  </svg>
                </div>
                <span className="gsi-material-button-contents">
                  {loading ? "Signing in..." : "Sign in with Google"}
                </span>
              </div>
            </button>
          </>
        )}
      </main>
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