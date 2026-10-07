/** Gmail DOM is a selection hint, never the authoritative email body or identity. */
export function gmailId(value?: string | null): string | null {
  if (!value) return null
  return /^[a-f0-9]{1,32}$/i.test(value) ? value.toLowerCase() : null
}
const lines = (doc: Document, body: string) => {
  const fragment = doc.createDocumentFragment()
  body.split("\n").forEach((line, i) => {
    if (i) fragment.append(doc.createElement("br"))
    if (line) fragment.append(doc.createTextNode(line))
  })
  return fragment
}
// Gmail adds these to a reply on its own; inserted text never goes inside them.
const GMAIL_KEPT = ".gmail_signature, .gmail_signature_prefix, .gmail_quote"
/** Visible reply boxes in the open thread; compose windows sit outside it. */
export function replyEditors(doc: Document): HTMLElement[] {
  const heading = doc.querySelector("h2.hP")
  const main =
    heading?.closest('[role="main"]') || doc.querySelector('[role="main"]')
  return Array.from(
    (main || doc).querySelectorAll<HTMLElement>(
      '[contenteditable="true"][role="textbox"]'
    )
  ).filter((el) => el.getClientRects().length > 0)
}
/** The reply box holding the user's cursor, else the only one open. */
export function targetReplyEditor(doc: Document): HTMLElement | null {
  const editors = replyEditors(doc)
  const range = doc.getSelection()?.rangeCount
    ? doc.getSelection()!.getRangeAt(0)
    : null
  return (
    (range && editors.find((el) => el.contains(range.startContainer))) ||
    (editors.length === 1 ? editors[0] : null)
  )
}
/**
 * Add a draft to a reply box as plain text without removing anything: at the
 * user's cursor, or above the signature and quoted text when there is none.
 */
export function insertIntoReplyEditor(editor: HTMLElement, body: string) {
  const doc = editor.ownerDocument
  const fragment = lines(doc, body)
  const last = fragment.lastChild
  const selection = doc.getSelection()
  const range = selection?.rangeCount ? selection.getRangeAt(0) : null
  const start = range?.startContainer
  const startElement =
    start?.nodeType === Node.ELEMENT_NODE
      ? (start as Element)
      : start?.parentElement
  const atCursor =
    range && editor.contains(start) && !startElement?.closest(GMAIL_KEPT)
  if (atCursor) {
    range.deleteContents()
    range.insertNode(fragment)
  } else editor.insertBefore(fragment, editor.firstChild)
  if (last && selection) {
    const after = doc.createRange()
    after.setStartAfter(last)
    after.collapse(true)
    selection.removeAllRanges()
    selection.addRange(after)
  }
}
/** True when a Gmail URL is showing one email thread rather than a list. */
export function gmailUrlShowsEmail(href?: string | null): boolean {
  if (!href?.startsWith("https://mail.google.com/")) return false
  const parts = new URL(href).hash.replace(/^#/, "").split("/")
  // Lists look like #inbox or #label/Work; an open thread adds its id: #inbox/FMfcgz…
  return parts.length >= 2 && /^[A-Za-z0-9_-]{16,}$/.test(parts.at(-1)!)
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
  // Only whether a reply box is showing; its contents are never read.
  // Gmail puts the reply box below the messages, not inside one of them.
  const replyEditorOpen = replyEditors(doc).length > 0
  return {
    threadId,
    messageIds,
    selectedMessageId: selected,
    subject: heading?.textContent?.trim() || null,
    accountEmail: account,
    replyEditorOpen
  }
}
