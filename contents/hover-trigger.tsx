import type { PlasmoCSConfig } from "plasmo"
import { useEffect, useState } from "react"

export const config: PlasmoCSConfig = {
  matches: ["<all_urls>"]
}

const FADE_START_PERCENT = 0.6 // starts fading in from here
const FADE_END_PERCENT = 1.0   // fully solid at the edge

const HoverTrigger = () => {
  const [proximity, setProximity] = useState(0) // 0 = far away, 1 = at the edge
  const [hovered, setHovered] = useState(false)

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      const percentAcross = e.clientX / window.innerWidth
      const raw =
        (percentAcross - FADE_START_PERCENT) /
        (FADE_END_PERCENT - FADE_START_PERCENT)
      setProximity(Math.min(1, Math.max(0, raw)))
    }

    window.addEventListener("mousemove", handleMouseMove)
    return () => window.removeEventListener("mousemove", handleMouseMove)
  }, [])

  const openChat = () => {
    chrome.runtime.sendMessage({ type: "OPEN_SIDE_PANEL" })
  }

  const visible = proximity > 0 || hovered
  const effectiveProximity = hovered ? 1 : proximity
  const eased = Math.pow(effectiveProximity, 3) // slow start, fast finish near the edge
  const opacity = 0.15 + 0.85 * eased
  const translateXPercent = 75 - 55 * eased
  const scale = 0.4 + 0.6 * eased

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        position: "fixed",
        top: "50%",
        right: 0,
        transform: `translateY(-50%) translateX(${translateXPercent}%) scale(${scale})`,
        transformOrigin: "right center",
        opacity,
        transition: "transform 0.1s linear, opacity 0.1s linear",
        zIndex: 999999,
        cursor: "pointer"
      }}
    >
      {/* 
        Button updated:
        - Width increased from 48px to 64px (wider)
        - Height increased from 48px to 56px (longer)
        - Padding & border-radius adjusted to preserve pill proportions
      */}
      <button
        onClick={openChat}
        style={{
          width: 64,
          height: 56,
          padding: "0 12px",
          border: "1px solid #ececec",
          borderRadius: 16,
          backgroundColor: "#ffffff",
          boxShadow: "0 6px 16px rgba(0,0,0,0.12)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 22,
          cursor: "pointer"
        }}
      >
        📥✨
      </button>
    </div>
  )
}

export default HoverTrigger