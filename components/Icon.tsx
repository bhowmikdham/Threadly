import type { CSSProperties } from "react"

const paths = {
  copy: "M8 8h12v13H8zM16 8V3H3v13h5",
  external: "M14 3h7v7M21 3l-11 11M10 4H4v16h16v-6",
  plus: "M12 5v14M5 12h14",
  send: "m6 12 6-6 6 6M12 6v13",
  menu: "M4 5h16v14H4zM9 5v14",
  close: "m6 6 12 12M6 18 18 6",
  edit: "m15 4 5 5M4 20l5-1L20 8l-5-5L4 14z",
  mail: "M3 5h18v14H3zM3 6l9 7 9-7",
  mic: "M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0zM6 10v2a6 6 0 0 0 12 0v-2M12 18v4M8 22h8",
  shield: "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6zM8 12l3 3 5-6",
  sparkle: "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z",
  search: "M16 16l5 5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  chevron: "m8 5 7 7-7 7",
  check: "m5 12 4 4L19 6",
  clock: "M12 7v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0"
}
export function Icon({
  name,
  size = 18,
  style
}: {
  name: keyof typeof paths
  size?: number
  style?: CSSProperties
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={style}>
      <path d={paths[name]} />
    </svg>
  )
}

// A source marker for Gmail content, kept separate from Threadly's own icons.
export function GmailIcon({
  size = 18,
  style
}: {
  size?: number
  style?: CSSProperties
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      aria-hidden="true"
      style={style}>
      <path fill="#4285F4" d="M2 5.3V18.6C2 19.4 2.6 20 3.4 20H6V8.3L2 5.3Z" />
      <path
        fill="#34A853"
        d="M18 8.3V20H20.6C21.4 20 22 19.4 22 18.6V5.3L18 8.3Z"
      />
      <path
        fill="#EA4335"
        d="M12 12.8L22 5.3C22 4 20.5 3.3 19.4 4.1L12 9.6L4.6 4.1C3.5 3.3 2 4 2 5.3L12 12.8Z"
      />
      <path fill="#C5221F" d="M2 5.3L6 8.3V5.1L4.6 4.1C3.5 3.3 2 4 2 5.3Z" />
      <path
        fill="#FBBC04"
        d="M18 8.3L22 5.3C22 4 20.5 3.3 19.4 4.1L18 5.1V8.3Z"
      />
    </svg>
  )
}
