// contents/hover-trigger.tsx
import type { PlasmoCSConfig } from "plasmo"
import { useEffect, useState } from "react"

import { Logo } from "../components/Icon"

export const config: PlasmoCSConfig = {
  matches: ["https://mail.google.com/*"]
}

// The tab starts sliding out once the pointer is in the right 40% of Gmail.
const REVEAL_FROM = 0.6
const TAB = 44

const palettes = {
  light: {
    background: "#ffffff",
    border: "#e3e2e8",
    ink: "#1b2135",
    hover: "#f4f3f8",
    tip: "#1f1f23",
    tipInk: "#ffffff",
    shadow: "0 1px 2px #1b213514, 0 8px 24px -8px #1b213540"
  },
  dark: {
    background: "#2b2b2f",
    border: "#3d3d42",
    ink: "#efebe3",
    hover: "#34343a",
    tip: "#efebe3",
    tipInk: "#1b2135",
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
  // At rest the tab is slightly tucked in and slides fully out as the pointer
  // approaches. It never changes size; hover or focus shows a label beside it.
  const tucked = open ? 0 : 6 * (1 - reveal)
  const motion = (value: string) => (calm ? "none" : value)

  return (
    <div
      style={{
        position: "fixed",
        top: "50%",
        right: 0,
        zIndex: 999999,
        display: "flex",
        alignItems: "center",
        transform: `translate(${tucked}px, -50%)`,
        transition: motion("transform 0.18s ease-out")
      }}>
      <span
        role="tooltip"
        id="threadly-launcher-tip"
        style={{
          position: "relative",
          marginRight: 12,
          padding: "7px 11px",
          borderRadius: 8,
          background: c.tip,
          color: c.tipInk,
          font: "500 13px/1.2 -apple-system, BlinkMacSystemFont, 'Google Sans', 'Segoe UI', Roboto, sans-serif",
          whiteSpace: "nowrap",
          pointerEvents: "none",
          opacity: open ? 1 : 0,
          transform: open ? "translateX(0)" : "translateX(4px)",
          transition: motion("opacity 0.15s ease-out, transform 0.15s ease-out")
        }}>
        Open Threadly
        <span
          aria-hidden="true"
          style={{
            position: "absolute",
            top: "50%",
            right: -4,
            width: 8,
            height: 8,
            background: c.tip,
            transform: "translateY(-50%) rotate(45deg)",
            borderRadius: 1
          }}
        />
      </span>
      <button
        type="button"
        aria-label="Open Threadly"
        aria-describedby="threadly-launcher-tip"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => chrome.runtime.sendMessage({ type: "OPEN_SIDE_PANEL" })}
        style={{
          display: "grid",
          placeItems: "center",
          width: TAB,
          height: TAB,
          padding: 0,
          border: `1px solid ${c.border}`,
          borderRight: 0,
          borderRadius: "12px 0 0 12px",
          background: open ? c.hover : c.background,
          color: c.ink,
          boxShadow: c.shadow,
          cursor: "pointer",
          outlineOffset: 2,
          transition: motion("background 0.15s")
        }}>
        <span
          style={{
            display: "flex",
            transform: open ? "scale(1.08)" : "scale(1)",
            transition: motion("transform 0.18s cubic-bezier(0.22, 1, 0.36, 1)")
          }}>
          <Logo height={10} />
        </span>
      </button>
    </div>
  )
}

export default HoverTrigger
