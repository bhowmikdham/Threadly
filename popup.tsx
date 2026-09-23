import { useState } from "react"

export default function Popup() {
  const [error, setError] = useState("")
  return (
    <main style={{ width: 260, padding: 20, fontFamily: "system-ui" }}>
      <h2>Threadly</h2>
      <p>Your email assistant, with you in control.</p>
      <button
        onClick={async () => {
          try {
            const [tab] = await chrome.tabs.query({
              active: true,
              currentWindow: true
            })
            if (tab?.id) await chrome.sidePanel.open({ tabId: tab.id })
            window.close()
          } catch {
            setError("Open Gmail and use the Threadly side panel.")
          }
        }}>
        Open assistant
      </button>
      {error && <p role="alert">{error}</p>}
    </main>
  )
}
