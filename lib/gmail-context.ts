/** Gmail DOM is a selection hint, never the authoritative email body or identity. */
export function gmailId(value?: string | null): string | null {
  if (!value) return null
  return /^[a-f0-9]{1,32}$/i.test(value) ? value.toLowerCase() : null
}
export function readGmailSelection(doc: Document, href: string) {
  const heading = doc.querySelector("h2.hP")
  const main =
    heading?.closest('[role="main"]') || doc.querySelector('[role="main"]')
  const scoped = main || doc
  const explicit = heading
    ? scoped
        .querySelector("[data-legacy-thread-id]")
        ?.getAttribute("data-legacy-thread-id")
    : null
  const hash = new URL(href).hash.split("/").at(-1)
  const threadId = heading ? gmailId(explicit) || gmailId(hash) : null
  const rows = Array.from(scoped.querySelectorAll("[data-legacy-message-id]"))
  const messageIds = [
    ...new Set(
      rows
        .map((r) => gmailId(r.getAttribute("data-legacy-message-id")))
        .filter(Boolean)
    )
  ] as string[]
  const focused = doc.activeElement?.closest("[data-legacy-message-id]")
  const expanded = rows.filter(
    (r) => r.getAttribute("aria-expanded") === "true"
  )
  const selected =
    gmailId(focused?.getAttribute("data-legacy-message-id")) ||
    (expanded.length === 1
      ? gmailId(expanded[0].getAttribute("data-legacy-message-id"))
      : null)
  const account =
    doc
      .querySelector('[aria-label*="Google Account:"]')
      ?.getAttribute("aria-label")
      ?.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i)?.[0] || null
  return {
    threadId,
    messageIds,
    selectedMessageId: selected,
    subject: heading?.textContent?.trim() || null,
    accountEmail: account
  }
}
