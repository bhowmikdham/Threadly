// contents/hover-trigger.tsx
import type { PlasmoCSConfig } from "plasmo"
import { useEffect, useState } from "react"

import { Logo } from "../components/Icon"

export const config: PlasmoCSConfig = {
  matches: ["https://mail.google.com/*"]
}

// The tab starts sliding out once the pointer is in the right 40% of Gmail.
const REVEAL_FROM = 0.6
const TAB = 46
const OPEN = 118

const palettes = {
  light: {
    background: "#ffffff",
    border: "#e3e2e8",
    ink: "#1b2135",
    shadow: "0 1px 2px #1b213514, 0 8px 24px -8px #1b213540"
  },
  dark: {
    background: "#2b2b2f",
    border: "#3d3d42",
    ink: "#efebe3",
    shadow: "0 1px 2px #00000066, 0 10px 28px -8px #000000aa"
  }
}

// Gmail's own theme decides ours: a dark page background means dark mode.
function gmailIsDark() {
  const [r, g, b] = (
    getComputedStyle(document.body).backgroundColor.match(/\d+/g) || [
      "255",
      "255",
      "255"
    ]
  ).map(Number)
  return 0.2126 * r + 0.7152 * g + 0.0722 * b < 96
}

const HoverTrigger = () => {
  const [reveal, setReveal] = useState(0),
    [open, setOpen] = useState(false),
    [dark, setDark] = useState(false),
    [calm, setCalm] = useState(false)

  useEffect(() => {
    const move = (e: MouseEvent) => {
      const across = e.clientX / window.innerWidth
      setReveal(
        Math.min(1, Math.max(0, (across - REVEAL_FROM) / (1 - REVEAL_FROM)))
      )
    }
    const theme = () => setDark(gmailIsDark())
    setCalm(matchMedia("(prefers-reduced-motion: reduce)").matches)
    theme()
    const timer = setInterval(theme, 5000)
    window.addEventListener("mousemove", move)
    return () => {
      clearInterval(timer)
      window.removeEventListener("mousemove", move)
    }
  }, [])

  const c = dark ? palettes.dark : palettes.light
  // At rest the tab is partly tucked in; it slides fully out as the pointer
  // approaches, and widens to show its name on hover or keyboard focus.
  const tucked = open ? 0 : 6 * (1 - reveal)

  return (
    <button
      type="button"
      aria-label="Open Threadly"
      title="Open Threadly"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      onClick={() => chrome.runtime.sendMessage({ type: "OPEN_SIDE_PANEL" })}
      style={{
        position: "fixed",
        top: "50%",
        right: 0,
        zIndex: 999999,
        display: "flex",
        alignItems: "center",
        gap: 9,
        width: open ? OPEN : TAB,
        height: 40,
        padding: "0 0 0 12px",
        overflow: "hidden",
        border: `1px solid ${c.border}`,
        borderRight: 0,
        borderRadius: "12px 0 0 12px",
        background: c.background,
        color: c.ink,
        boxShadow: c.shadow,
        cursor: "pointer",
        outlineOffset: 2,
        font: "600 13px/1 -apple-system, BlinkMacSystemFont, 'Google Sans', 'Segoe UI', Roboto, sans-serif",
        letterSpacing: "-0.01em",
        whiteSpace: "nowrap",
        transform: `translate(${tucked}px, -50%)`,
        transition: calm
          ? "none"
          : "width 0.22s cubic-bezier(0.22, 1, 0.36, 1), transform 0.18s ease-out"
      }}>
      <span style={{ display: "flex", flex: "none" }}>
        <Logo height={10} />
      </span>
      <span
        style={{
          opacity: open ? 1 : 0,
          transition: calm ? "none" : "opacity 0.15s"
        }}>
        Threadly
      </span>
    </button>
  )
}

export default HoverTrigger
