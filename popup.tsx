// popup.tsx
import { useState } from "react"

/**
 * Extension Browser Action Popup Component
 * Simple entry point for extension options and web links.
 */
function IndexPopup() {
  const [data, setData] = useState<string>("")

  return (
    <div style={{ padding: 16 }}>
      <h2>
        Welcome to{" "}
        <a href="https://www.plasmo.com" target="_blank" rel="noreferrer">
          Mail Mind
        </a>
      </h2>
      {/* Input control for quick query or token entry testing */}
      <input 
        onChange={(e) => setData(e.target.value)} 
        value={data} 
        placeholder="Quick text input..."
      />
      <div style={{ marginTop: 8 }}>
        <a href="https://docs.plasmo.com" target="_blank" rel="noreferrer">
          View Extension Documentation
        </a>
      </div>
    </div>
  )
}

export default IndexPopup