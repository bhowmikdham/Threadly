import type { CSSProperties, ReactNode } from "react"

// Glyphs from the Threadly icon set (assets/icons): 16px grid, 1.5px round
// strokes, currentColor. Names are kept stable for the components that use them.
const glyphs = {
  copy: (
    <>
      <rect x="5.4" y="5.4" width="8.2" height="8.2" rx="1.6" />
      <path d="M10.6 5.4V4a1.6 1.6 0 0 0-1.6-1.6H4A1.6 1.6 0 0 0 2.4 4v5a1.6 1.6 0 0 0 1.6 1.6h1.4" />
    </>
  ),
  external: (
    <>
      <path d="M9.4 2.6h4v4" />
      <path d="M13.4 2.6 7.6 8.4" />
      <path d="M11.6 9.6v2.2a1.6 1.6 0 0 1-1.6 1.6H4.2a1.6 1.6 0 0 1-1.6-1.6V6a1.6 1.6 0 0 1 1.6-1.6h2.2" />
    </>
  ),
  plus: (
    <>
      <path d="M8 3.2v9.6" />
      <path d="M3.2 8h9.6" />
    </>
  ),
  send: (
    <>
      <path d="M8 13.2V3" />
      <path d="M3.8 7.2 8 3l4.2 4.2" />
    </>
  ),
  menu: (
    <>
      <rect x="1.8" y="2.8" width="12.4" height="10.4" rx="1.8" />
      <path d="M5.6 2.8v10.4" />
    </>
  ),
  close: (
    <>
      <path d="M4.4 4.4 11.6 11.6" />
      <path d="M11.6 4.4 4.4 11.6" />
    </>
  ),
  edit: (
    <>
      <path d="M2.6 9.6 10 2.2l3.4 3.4-7.4 7.4H2.6z" />
      <path d="M8.4 3.8l3.4 3.4" />
    </>
  ),
  mail: (
    <>
      <rect x="1.8" y="3.2" width="12.4" height="9.6" rx="1.8" />
      <path d="m2.4 4.4 5.6 4.2 5.6-4.2" />
    </>
  ),
  mic: (
    <>
      <rect x="6" y="1.8" width="4" height="7.4" rx="2" />
      <path d="M3.6 7.6a4.4 4.4 0 0 0 8.8 0" />
      <path d="M8 12v2.2" />
    </>
  ),
  "mic-off": (
    <>
      <rect x="6" y="1.8" width="4" height="7.4" rx="2" />
      <path d="M3.6 7.6a4.4 4.4 0 0 0 8.8 0" />
      <path d="M8 12v2.2" />
      <path d="M2.4 2.4 13.6 13.6" />
    </>
  ),
  shield: (
    <>
      <path d="M8 1.8 13 3.6v4c0 3.2-2.4 5.4-5 6.6-2.6-1.2-5-3.4-5-6.6v-4z" />
      <path d="m5.8 8 1.6 1.6 2.8-3" />
    </>
  ),
  search: (
    <>
      <circle cx="7" cy="7" r="4.4" />
      <path d="m10.3 10.3 3.3 3.3" />
    </>
  ),
  chevron: <path d="m6.2 3.6 4.4 4.4-4.4 4.4" />,
  check: <path d="m3.2 8.4 3 3 6.6-6.8" />,
  clock: (
    <>
      <circle cx="8" cy="8" r="5.8" />
      <path d="M8 4.8V8l2.2 1.4" />
    </>
  ),
  trash: (
    <>
      <path d="M2.6 4.2h10.8" />
      <path d="M6.2 4.2V2.8h3.6v1.4" />
      <path d="M3.8 4.2l.6 8.4a1.4 1.4 0 0 0 1.4 1.4h4.4a1.4 1.4 0 0 0 1.4-1.4l.6-8.4" />
    </>
  ),
  settings: (
    <>
      <path d="M2.4 4.6h6.2M12.4 4.6h1.2M2.4 11.4h1.2M7.4 11.4h6.2" />
      <circle cx="10.5" cy="4.6" r="1.9" />
      <circle cx="5.5" cy="11.4" r="1.9" />
    </>
  ),
  signout: (
    <>
      <path d="M6.4 2.6H3.8a1.2 1.2 0 0 0-1.2 1.2v8.4a1.2 1.2 0 0 0 1.2 1.2h2.6" />
      <path d="M10.4 11 13.4 8l-3-3" />
      <path d="M13.4 8H6.2" />
    </>
  ),
  moon: <path d="M13.4 9.8A5.8 5.8 0 0 1 6.2 2.6a5.8 5.8 0 1 0 7.2 7.2z" />,
  sun: (
    <>
      <circle cx="8" cy="8" r="3.1" />
      <path d="M8 1.2v1.6M8 13.2v1.6M1.2 8h1.6M13.2 8h1.6M3.2 3.2l1.15 1.15M11.65 11.65l1.15 1.15M12.8 3.2l-1.15 1.15M4.35 11.65 3.2 12.8" />
    </>
  ),
  calendar: (
    <>
      <rect x="2" y="3.4" width="12" height="10.6" rx="1.8" />
      <path d="M2 6.6h12" />
      <path d="M5.4 2v2.4" />
      <path d="M10.6 2v2.4" />
      <circle cx="8" cy="10.4" r="1.4" fill="currentColor" stroke="none" />
    </>
  )
} satisfies Record<string, ReactNode>

// Bhowmik's sparkle, unchanged, on its original 24px grid.
const sparkle = "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z"

export function Icon({
  name,
  size = 18,
  style
}: {
  name: keyof typeof glyphs | "sparkle"
  size?: number
  style?: CSSProperties
}) {
  const spark = name === "sparkle"
  return (
    <svg
      width={size}
      height={size}
      viewBox={spark ? "0 0 24 24" : "0 0 16 16"}
      fill="none"
      stroke="currentColor"
      strokeWidth={spark ? 1.65 : 1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={style}>
      {spark ? <path d={sparkle} /> : glyphs[name]}
    </svg>
  )
}

// The Threadly logo: two hooked loops of one thread. Traced from the brand
// artwork; colour follows currentColor so it reads in both appearances.
export function Logo({ height = 16 }: { height?: number }) {
  return (
    <svg
      className="threadly-logo"
      height={height}
      width={(height * 434) / 184}
      viewBox="76 44 434 184"
      fill="none"
      stroke="currentColor"
      strokeWidth="34"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true">
      <path d="M96 132C96 90 134 64 184 64c54 0 90 40 90 86 0 38-22 58-54 58-38 0-68-26-70-76" />
      <path d="M326 130c10 50 48 78 92 78 44 0 72-28 72-68 0-46-38-76-88-76-58 0-100 20-122 44" />
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

// Google Calendar's own mark for the Calendar connector, like GmailIcon.
export function GoogleCalendarIcon({
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
      viewBox="0 0 200 200"
      aria-hidden="true"
      style={style}>
      <path fill="#fff" d="M152.6 47.4H47.4v105.2h105.2z" />
      <path fill="#EA4335" d="M152.6 200 200 152.6h-47.4z" />
      <path fill="#FBBC04" d="M200 47.4h-47.4v105.2H200z" />
      <path fill="#34A853" d="M152.6 152.6H47.4V200h105.2z" />
      <path
        fill="#188038"
        d="M0 152.6v31.6C0 192.9 7.1 200 15.8 200h31.6v-47.4z"
      />
      <path
        fill="#1967D2"
        d="M200 47.4V15.8C200 7.1 192.9 0 184.2 0h-31.6v47.4z"
      />
      <path
        fill="#4285F4"
        d="M152.6 0H15.8C7.1 0 0 7.1 0 15.8v136.8h47.4V47.4h105.2z"
      />
      <text
        x="100"
        y="133"
        fill="#4285F4"
        fontFamily="'Google Sans', Roboto, Arial, sans-serif"
        fontSize="78"
        fontWeight="700"
        textAnchor="middle">
        31
      </text>
    </svg>
  )
}
