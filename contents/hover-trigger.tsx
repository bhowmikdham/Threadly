// contents/hover-trigger.tsx
import type { PlasmoCSConfig } from "plasmo"
import { useEffect, useState } from "react"

import { Logo } from "../components/Icon"

export const config: PlasmoCSConfig = {
  matches: ["https://mail.google.com/*"]
}

// The tab starts sliding out once the pointer is in the right 40% of Gmail.
const REVEAL_FROM = 0.6
// Small and quiet at rest; full size with a soft blue tint on hover.
const REST = { width: 30, height: 34, logo: 8 }
const OPEN = { width: 44, height: 44, logo: 10 }

const palettes = {
  light: {
    background: "#ffffff",
    border: "#e3e2e8",
    ink: "#1b2135",
    hover: "#eef3fe",
    hoverBorder: "#b9cdf7",
    ring: "#1a73e8",
    tip: "#1f1f23",
    tipInk: "#ffffff",
    shadow: "0 1px 2px #1b213514, 0 8px 24px -8px #1b213540"
  },
  dark: {
    background: "#2b2b2f",
    border: "#3d3d42",
    ink: "#efebe3",
    hover: "#2c3447",
    hoverBorder: "#4d628f",
    ring: "#8ab4f8",
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
    [keyboard, setKeyboard] = useState(false),
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
  // At rest the tab is small, colourless and slightly tucked in; it slides out
  // as the pointer approaches, and grows with a soft blue tint and a label on
  // hover or keyboard focus.
  const tucked = open ? 0 : 5 * (1 - reveal)
  const size = open ? OPEN : REST
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
        onFocus={(e) => {
          // Only keyboard focus opens it; a mouse click leaves no focus state.
          const visible = e.currentTarget.matches(":focus-visible")
          setKeyboard(visible)
          if (visible) setOpen(true)
        }}
        onBlur={() => {
          setKeyboard(false)
          setOpen(false)
        }}
        onClick={(e) => {
          chrome.runtime.sendMessage({ type: "OPEN_SIDE_PANEL" })
          e.currentTarget.blur()
          setOpen(false)
        }}
        style={{
          display: "grid",
          placeItems: "center",
          width: size.width,
          height: size.height,
          padding: 0,
          border: `1px solid ${open ? c.hoverBorder : c.border}`,
          borderRight: 0,
          borderRadius: "11px 0 0 11px",
          background: open ? c.hover : c.background,
          color: c.ink,
          boxShadow: c.shadow,
          cursor: "pointer",
          outline: keyboard ? `2px solid ${c.ring}` : "none",
          outlineOffset: 2,
          transition: motion(
            "width 0.2s cubic-bezier(0.22, 1, 0.36, 1), height 0.2s cubic-bezier(0.22, 1, 0.36, 1), background 0.15s, border-color 0.15s"
          )
        }}>
        <span
          style={{
            display: "flex",
            transition: motion("transform 0.18s cubic-bezier(0.22, 1, 0.36, 1)")
          }}>
          <Logo height={size.logo} />
        </span>
      </button>
    </div>
  )
}

export default HoverTrigger
