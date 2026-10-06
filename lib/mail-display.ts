// These transformations are only for compact display text. Never use their
// output as a source body, citation, message identifier or action payload.
export function mailText(value: string | null | undefined): string {
  let text = typeof value === "string" ? value.slice(0, 20000) : ""
  const decoder = document.createElement("textarea")
  for (let pass = 0; pass < 3; pass++) {
    const decoded = text.replace(
      /&(?:#[0-9]{1,7}|#x[0-9a-f]{1,6}|[a-z][a-z0-9]{1,31});/gi,
      (entity) => {
        // Decode entities individually, never parse an email's HTML or load URLs.
        decoder.innerHTML = entity
        return decoder.value
      }
    )
    if (decoded === text) break
    text = decoded
  }
  return (
    text
      // Remove hidden preheader padding, while preserving isolated ZWNJ/ZWJ
      // within real words and emoji sequences.
      .replace(/[\u200c\u200d\s]{2,}/g, (run) =>
        /[\u200c\u200d]/.test(run) ? " " : run
      )
      .replace(/[\u00ad\u200b\u2060\ufeff]/g, "")
      .replace(/[\u202a-\u202e\u2066-\u2069]/g, "")
      .replace(/[^\S\n]+/g, " ")
      .trim()
  )
}

const trackingUrl = (value: string) => {
  try {
    const url = new URL(value)
    return (
      value.length > 120 ||
      /\/(?:track(?:ing)?|click|open|unsubscribe)(?:[/?#]|$)/i.test(
        url.pathname
      ) ||
      [...url.searchParams.keys()].some((key) =>
        /^(?:utm_|mc_|trk|tracking)/i.test(key)
      )
    )
  } catch {
    return false
  }
}

export function mailExcerpt(
  value: string | null | undefined,
  limit = 240
): string {
  let text = mailText(value)
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/gi, (whole, label, url) =>
      trackingUrl(url) ? label : whole
    )
    .replace(/https?:\/\/[^\s<>]+/gi, (url) => (trackingUrl(url) ? " " : url))
    // A footer must start a separate line or sentence, after meaningful text.
    .replace(
      /([\n]|[.!?]\s+)(?:unsubscribe|manage (?:your )?(?:email )?preferences|view (?:this email )?in (?:your )?browser)\b[\s\S]*$/i,
      "$1"
    )
    .replace(/\s+/g, " ")
    .trim()
  if (text.length > limit) text = `${text.slice(0, limit - 1).trimEnd()}…`
  return text
}

export function displayTimezone(value?: string): string {
  for (const zone of [
    value,
    Intl.DateTimeFormat().resolvedOptions().timeZone,
    "UTC"
  ]) {
    if (!zone) continue
    try {
      return new Intl.DateTimeFormat("en", { timeZone: zone }).resolvedOptions()
        .timeZone
    } catch {
      // Legacy pages can omit a timezone; malformed zones must not break cards.
    }
  }
  return "UTC"
}

export function mailTimestamp(
  value: string,
  timezone: string,
  locale?: string
): string | null {
  // Date-only and offset-free timestamps have no authoritative instant. Do not
  // silently reinterpret them in the computer's zone.
  if (typeof value !== "string" || !/T.*(?:Z|[+-]\d{2}:?\d{2})$/i.test(value))
    return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return new Intl.DateTimeFormat(locale, {
    timeZone: displayTimezone(timezone),
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short"
  }).format(date)
}

export function mailDateBound(value: unknown, timezone: string): string | null {
  if (typeof value !== "string") return null
  // Calendar-date filters are literal days; parsing as UTC shifts them westward.
  const isDay = /^\d{4}-\d{2}-\d{2}$/.test(value)
  if (!isDay) return mailTimestamp(value, timezone)
  const date = new Date(`${value}T12:00:00Z`)
  if (Number.isNaN(date.getTime())) return null
  return new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC"
  }).format(date)
}
