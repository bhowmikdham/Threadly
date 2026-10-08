import type { PlasmoCSConfig } from "plasmo"

import {
  categoryDisplay,
  categoryIcon,
  cooldownCodes,
  priorityIcon,
  priorityTip,
  replyIcon,
  validClassification,
  type Classification
} from "../lib/classification"
import { gmailAccountEmail, gmailId, gmailIsDark } from "../lib/gmail-context"

export const config: PlasmoCSConfig = {
  matches: ["https://mail.google.com/*"],
  run_at: "document_idle"
}

// Priority + reply before each inbox row's subject, category after it, from
// the classification service. Only rows that stay on screen for a second are
// classified, two at a time; results are kept until their valid_until.
type Result = {
  state: "queued" | "loading" | "done" | "failed"
  value?: Classification
  until: number
  attempts: number
  code?: string
}
const results = new Map<string, Result>()
const threads = new Map<string, { thread: string; last: string }>()
// Rows on screen show any result already known; rows that have stayed there
// for SETTLE_MS are worth classifying, so a fast scroll past costs nothing.
const SETTLE_MS = 1000
const onScreen = new Set<Element>()
const visible = new Set<Element>()
const settling = new Map<Element, number>()
const observed = new WeakSet<Element>()
const queue: string[] = []
const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
let account: string | null = null
let generation = 0
let inFlight = 0
let pausedUntil = 0
let resumeTimer = 0
let stopped = false
// The AI service failing is retried a few times per row, then left blank.
const providerTries = new Map<string, number>()
const PROVIDER_TRIES = 3

const root = document.documentElement
const inInbox = () => /^#inbox(\/p\d+)?(\?.*)?$/.test(location.hash || "#inbox")
const show = (on: boolean) =>
  root.setAttribute("data-threadly-badges", on ? "on" : "off")
show(false)

function rowKey(row: Element) {
  const ids = row.querySelector("[data-legacy-thread-id]")
  const thread = gmailId(ids?.getAttribute("data-legacy-thread-id"))
  if (!thread || !account) return null
  // New mail changes the last message, so it gets a fresh classification.
  const last = ids.getAttribute("data-legacy-last-message-id") || ""
  const key = `${account}|${thread}|${last}|${timeZone}`
  threads.set(key, { thread, last })
  return key
}
function slots(row: Element) {
  const line = row.querySelector(".xT")
  const subject = line?.querySelector(":scope > .y6")
  if (!line || !subject) return null
  let left = line.querySelector<HTMLElement>(":scope > .tl-left")
  let right = line.querySelector<HTMLElement>(":scope > .tl-right")
  if (!left) {
    left = document.createElement("span")
    left.className = "tl-slot tl-left"
    line.insertBefore(left, subject)
  }
  if (!right) {
    right = document.createElement("span")
    right.className = "tl-slot tl-right"
    line.append(right)
  }
  return { left, right }
}
function render(row: Element) {
  const key = rowKey(row)
  const target = slots(row)
  if (!key || !target) return
  const r = results.get(key)
  const labels =
    r?.state === "done" &&
    r.until > Date.now() &&
    r.value.status === "classified"
      ? r.value.labels
      : null
  const state = labels
    ? `${labels.priority}|${labels.needs_reply}|${labels.action}|${labels.category}`
    : "empty"
  if (target.left.dataset.tlState === state) return
  target.left.dataset.tlState = state
  // Loading, needs review, skipped and failed all show nothing, never a guess.
  for (const slot of [target.left, target.right]) {
    slot.replaceChildren()
    slot.removeAttribute("data-tl-tip")
    slot.removeAttribute("aria-label")
    slot.removeAttribute("role")
  }
  if (!labels) return
  const tip = priorityTip(labels)
  target.left.innerHTML =
    `<span class="tl-priority tl-${labels.priority.toLowerCase()}">${priorityIcon[labels.priority]}</span>` +
    (labels.needs_reply
      ? `<span class="tl-reply">${replyIcon}</span>`
      : '<span class="tl-hairline"></span>')
  target.right.innerHTML = categoryIcon[labels.category]
  for (const [slot, text] of [
    [target.left, tip],
    [target.right, categoryDisplay[labels.category]]
  ] as const) {
    slot.setAttribute("role", "img")
    slot.setAttribute("aria-label", text)
    slot.dataset.tlTip = text
  }
}

function schedule() {
  if (stopped || !inInbox() || document.visibilityState !== "visible") return
  if (Date.now() < pausedUntil) return
  const now = Date.now()
  const rows = Array.from(visible).filter((row) => row.isConnected)
  for (const row of rows) {
    const key = rowKey(row)
    if (!key) continue
    const r = results.get(key)
    if (
      !r ||
      ((r.state === "done" || r.state === "failed") && r.until <= now)
    ) {
      results.set(key, {
        state: "queued",
        until: 0,
        attempts: r?.attempts ?? 0
      })
      queue.push(key)
    }
  }
  pump()
}
function pump() {
  while (
    inFlight < 2 &&
    queue.length &&
    !stopped &&
    Date.now() >= pausedUntil
  ) {
    const key = queue.shift()
    const r = results.get(key)
    if (r?.state === "queued") void classify(key, r)
  }
}
// The server said to wait: everything waits. Rows not yet answered go back to
// pending and are asked again afterwards only if they're still on screen.
function pauseFor(seconds: number) {
  const jitter = 250 + Math.random() * 750
  pausedUntil = Math.max(pausedUntil, Date.now() + seconds * 1000 + jitter)
  for (const key of queue.splice(0)) results.delete(key)
  clearTimeout(resumeTimer)
  resumeTimer = window.setTimeout(schedule, pausedUntil - Date.now() + 10)
}
const retry = (key: string, r: Result, ms: number) => {
  results.set(key, { ...r, state: "queued", attempts: r.attempts + 1 })
  setTimeout(() => {
    queue.push(key)
    pump()
  }, ms)
}
async function classify(key: string, r: Result) {
  const run = generation
  const { thread: threadId, last } = threads.get(key)
  inFlight += 1
  results.set(key, { ...r, state: "loading" })
  let reply: any
  try {
    reply = await chrome.runtime.sendMessage({
      type: "THREADLY_CLASSIFY",
      threadId,
      lastMessageId: last,
      timeZone,
      accountEmail: account
    })
  } catch {
    // The extension was reloaded; this page's script is no longer connected.
    stopped = true
    show(false)
    return
  } finally {
    inFlight -= 1
  }
  if (run !== generation) return
  const soon = Date.now() + 5 * 60 * 1000
  const provider = reply?.code === "classification_provider_unavailable"
  if (reply?.ok && validClassification(reply.data, threadId)) {
    providerTries.delete(key)
    results.set(key, {
      state: "done",
      value: reply.data,
      until: Date.parse(reply.data.valid_until),
      attempts: 0
    })
    show(true)
  } else if (
    cooldownCodes.includes(reply?.code) &&
    !(provider && (providerTries.get(key) || 0) >= PROVIDER_TRIES - 1)
  ) {
    if (provider) providerTries.set(key, (providerTries.get(key) || 0) + 1)
    results.delete(key)
    pauseFor(reply.retryAfterSeconds || 5)
  } else if (
    [
      "classification_source_changed",
      "classification_expired",
      "classification_release_changed"
    ].includes(reply?.code) &&
    r.attempts < 1
  ) {
    retry(key, r, 0)
  } else if (
    ["classification_disabled", "classification_not_configured"].includes(
      reply?.code
    )
  ) {
    // The service is off: hide badges and don't keep asking.
    results.delete(key)
    pausedUntil = Date.now() + 10 * 60 * 1000
    show(false)
  } else if (["login_required", "account_mismatch"].includes(reply?.code)) {
    results.delete(key)
    pausedUntil = Date.now() + 30 * 1000
    show(false)
  } else {
    results.set(key, {
      state: "failed",
      until: soon,
      attempts: 0,
      code: reply?.code || "no_response"
    })
  }
  for (const row of onScreen) if (row.isConnected) render(row)
  pump()
  report()
}
// For whoever is checking in DevTools: why rows on screen do or don't have
// badges. Printed once each time the rows on screen have all finished.
let lastReport = ""
function report() {
  // Mid-pause, waiting rows have no result yet; report once they settle.
  if (Date.now() < pausedUntil) return
  const counts: Record<string, number> = {}
  for (const row of visible) {
    const key = row.isConnected && rowKey(row)
    const r = key && results.get(key)
    if (!r) continue
    if (r.state === "queued" || r.state === "loading") return
    const label =
      r.state === "failed"
        ? `failed (${r.code})`
        : r.value.status.replace("_", " ")
    counts[label] = (counts[label] || 0) + 1
  }
  const text = Object.entries(counts)
    .map(([label, n]) => `${n} ${label}`)
    .join(" · ")
  if (text && text !== lastReport) {
    lastReport = text
    console.info(`Threadly badges: ${text}`)
  }
}

// Gmail rebuilds rows freely, so every change re-finds rows and re-draws them.
const watcher = new IntersectionObserver((entries) => {
  for (const e of entries) {
    const row = e.target
    if (e.isIntersecting) {
      onScreen.add(row)
      render(row)
      if (!visible.has(row) && !settling.has(row))
        settling.set(
          row,
          window.setTimeout(() => {
            settling.delete(row)
            visible.add(row)
            schedule()
          }, SETTLE_MS)
        )
    } else {
      onScreen.delete(row)
      visible.delete(row)
      clearTimeout(settling.get(row))
      settling.delete(row)
    }
  }
})
let pending = 0
function scan() {
  pending = 0
  const now = gmailAccountEmail(document)?.toLowerCase() || null
  if (now !== account) {
    // Another account's results must never show here.
    account = now
    generation += 1
    results.clear()
    queue.length = 0
    providerTries.clear()
    // The background still holds any server-side pause for the new account.
    pausedUntil = 0
    clearTimeout(resumeTimer)
    show(false)
  }
  root.setAttribute(
    "data-threadly-theme",
    gmailIsDark(document) ? "dark" : "light"
  )
  if (!inInbox()) return
  for (const row of document.querySelectorAll('[role="main"] tr.zA')) {
    if (!observed.has(row)) {
      observed.add(row)
      watcher.observe(row)
    }
    if (onScreen.has(row)) render(row)
  }
  schedule()
}
const rescan = () => {
  if (!pending) pending = window.setTimeout(scan, 150)
}
new MutationObserver(rescan).observe(document.body, {
  childList: true,
  subtree: true
})
window.addEventListener("hashchange", rescan)
document.addEventListener("visibilitychange", rescan)
// Expired badges clear, then refresh while their rows are still on screen.
setInterval(scan, 30 * 1000)
scan()

// One Gmail-style tooltip; rows clip anything drawn inside them.
const tooltip = document.createElement("div")
tooltip.className = "tl-tooltip"
tooltip.setAttribute("role", "tooltip")
document.addEventListener("mouseover", (e) => {
  const slot = (e.target as Element).closest?.<HTMLElement>("[data-tl-tip]")
  if (!slot) return tooltip.remove()
  tooltip.textContent = slot.dataset.tlTip
  document.body.append(tooltip)
  const box = slot.getBoundingClientRect()
  tooltip.style.left = `${Math.max(8, box.left + box.width / 2 - tooltip.offsetWidth / 2)}px`
  tooltip.style.top = `${box.bottom + 6}px`
})

const style = document.createElement("style")
style.textContent = `
[data-threadly-badges="off"] .tl-slot { display: none; }
.tl-slot { display: inline-flex; align-items: center; flex: 0 0 auto; height: 20px; color: #5f6368; box-sizing: border-box; }
.tl-left { width: 62px; gap: 12px; padding: 0 6px 0 4px; }
.tl-right { width: 56px; justify-content: center; }
.tl-slot svg { display: block; flex: 0 0 16px; }
.tl-priority { display: inline-flex; }
.tl-high { color: #c5372c; } .tl-medium { color: #a2620a; } .tl-low { color: #5f6368; }
.tl-reply { display: inline-flex; color: #202124; }
.tl-hairline { width: 5px; height: 1px; margin: 0 5.5px; background: currentColor; opacity: .45; }
.tl-tooltip { position: fixed; z-index: 2147483647; pointer-events: none; padding: 4px 8px; border-radius: 4px; background: #3c4043; color: #fff; font: 500 12px/16px "Google Sans", Roboto, Arial, sans-serif; white-space: nowrap; }
[data-threadly-theme="dark"] .tl-slot { color: #9aa0a6; }
[data-threadly-theme="dark"] .tl-high { color: #f28b82; }
[data-threadly-theme="dark"] .tl-medium { color: #fdd663; }
[data-threadly-theme="dark"] .tl-low { color: #9aa0a6; }
[data-threadly-theme="dark"] .tl-reply { color: #e8eaed; }
[data-threadly-theme="dark"] .tl-tooltip { background: #e8eaed; color: #202124; }
`
document.head.append(style)
