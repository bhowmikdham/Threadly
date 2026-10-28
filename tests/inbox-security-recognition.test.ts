import { afterEach, expect, it, vi } from "vitest"

// Exercise the real content-script queue/render/ack path in a synthetic DOM.
// Animation timing remains a packaged-browser acceptance check.
const mutations: MutationObserver[] = []
afterEach(() => {
  for (const observer of mutations.splice(0)) observer.disconnect()
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})
it("updates a recognised row immediately, across reuse and a stale in-flight response; dismissal stays high", async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date("2026-10-28T12:00:00Z"))
  location.hash = "#inbox"
  Object.defineProperty(document, "visibilityState", {
    value: "visible",
    configurable: true
  })
  document.body.innerHTML = `<div aria-label="Google Account: Tester (tester@example.test)"></div><main role="main"><table><tr class="zA"><td><div class="xT"><div class="y6"><span class="bog" data-legacy-thread-id="aaa" data-legacy-last-message-id="bbb">Hertz: Confirm your sign-in</span></div></div></td></tr></table></main>`
  const NativeMutationObserver = MutationObserver
  vi.stubGlobal(
    "MutationObserver",
    class extends NativeMutationObserver {
      constructor(callback: MutationCallback) {
        super(callback)
        mutations.push(this)
      }
    }
  )
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(private callback: any) {}
      observe(row: Element) {
        this.callback([{ target: row, isIntersecting: true }])
      }
    }
  )
  vi.stubGlobal("matchMedia", () => ({
    matches: true,
    addEventListener: vi.fn()
  }))
  const listeners: any[] = []
  let pending: ((reply: any) => void) | null = null,
    hold = false,
    saved = false,
    rejectAck = false
  const response = () => {
    const id = document
      .querySelector(".bog")!
      .getAttribute("data-legacy-last-message-id")!
    const key = (
      id === "bbb" ? "a" : id === "ccc" ? "b" : id === "ddd" ? "c" : "d"
    ).repeat(64)
    return {
      ok: true,
      data: {
        schema_version: "email-classification-with-action.v1",
        thread_id: "aaa",
        status: "classified",
        labels: {
          needs_reply: false,
          priority: "High",
          category: "it",
          action: "review"
        },
        valid_until: new Date(Date.now() + 45_000).toISOString(),
        source: "live_gmail",
        persisted: false,
        source_message_ids: [id],
        latest_incoming: {
          message_id: id,
          timestamp_source: "gmail.internalDate"
        },
        security_priority: {
          message_id: id,
          priority_if_recognised: "Low",
          evidence: [id]
        }
      },
      awareness: saved && id === "bbb" ? null : { key, receivedAt: Date.now() },
      security: {
        key,
        receivedAt: Date.now(),
        state: saved && id === "bbb" ? "recognised" : null
      }
    }
  }
  const sendMessage = vi.fn(async (message: any) => {
    if (message.type === "THREADLY_GET_INBOX_MARKS")
      return { preferences: { enabled: true, words: true } }
    if (message.type === "THREADLY_CLASSIFY") {
      if (hold)
        return new Promise((resolve) => {
          pending = resolve
        })
      return response()
    }
    if (message.type === "THREADLY_SECURITY_ACK") {
      if (rejectAck) return { ok: false }
      if (message.state === "recognised") saved = true
      return {
        ok: true,
        key: message.lastMessageId === "bbb" ? "a".repeat(64) : "b".repeat(64),
        state: message.state
      }
    }
  })
  vi.stubGlobal("chrome", {
    runtime: {
      id: "extension",
      sendMessage,
      onMessage: { addListener: (listener: any) => listeners.push(listener) }
    }
  })
  await import("../contents/inbox-badges")
  await vi.advanceTimersByTimeAsync(1300)
  expect(document.querySelector(".tl-security-cue")).not.toBeNull()
  expect(
    document.querySelector(".tl-left")!.getAttribute("aria-label")
  ).toContain("High priority")
  rejectAck = true
  ;(
    document.querySelector(".tl-security-recognised") as HTMLButtonElement
  ).click()
  await vi.advanceTimersByTimeAsync(1)
  expect(document.querySelector(".tl-security-cue")!.textContent).toContain(
    "Could not save"
  )
  expect(
    document.querySelector(".tl-left")!.getAttribute("aria-label")
  ).toContain("High priority")
  rejectAck = false
  ;(
    document.querySelector(".tl-security-recognised") as HTMLButtonElement
  ).click()
  await vi.advanceTimersByTimeAsync(1)
  expect(document.querySelector(".tl-security-cue")).toBeNull()
  expect(
    document.querySelector(".tl-left")!.getAttribute("aria-label")
  ).toContain("Low priority")
  // Force reclassification while a deliberately stale unrecognised reply waits.
  hold = true
  await vi.advanceTimersByTimeAsync(60_000)
  expect(pending).not.toBeNull()
  const stale = response()
  stale.security.state = null
  stale.awareness = { key: "a".repeat(64), receivedAt: Date.now() }
  pending!(stale)
  await vi.advanceTimersByTimeAsync(1)
  expect(
    document.querySelector(".tl-left")!.getAttribute("aria-label")
  ).toContain("Low priority")
  expect(document.querySelector(".tl-security-cue")).toBeNull()
  // A new message gets its own high priority and question; dismiss is not recognition.
  hold = false
  document
    .querySelector(".bog")!
    .setAttribute("data-legacy-last-message-id", "ccc")
  await vi.advanceTimersByTimeAsync(500)
  expect(document.querySelector(".tl-security-cue")).not.toBeNull()
  ;(
    document.querySelector(".tl-security-dismissed") as HTMLButtonElement
  ).click()
  await vi.advanceTimersByTimeAsync(1)
  expect(document.querySelector(".tl-security-cue")).toBeNull()
  expect(
    document.querySelector(".tl-left")!.getAttribute("aria-label")
  ).toContain("High priority")
  // Classifier cues cannot override the existing forwarded/marketing exclusions.
  document.querySelector(".bog")!.textContent = "Your verification code"
  document
    .querySelector(".bog")!
    .setAttribute("data-legacy-last-message-id", "ddd")
  await vi.advanceTimersByTimeAsync(500)
  expect(document.querySelector(".tl-security-cue")!.textContent).toContain(
    "Possible account activity"
  )
  document.querySelector(".bog")!.textContent = "Fwd: Your verification code"
  document
    .querySelector(".bog")!
    .setAttribute("data-legacy-last-message-id", "ddd")
  await vi.advanceTimersByTimeAsync(500)
  expect(document.querySelector(".tl-security-cue")).toBeNull()
  document.querySelector(".bog")!.textContent =
    "Sign-in confirmation newsletter"
  document
    .querySelector(".bog")!
    .setAttribute("data-legacy-last-message-id", "eee")
  await vi.advanceTimersByTimeAsync(500)
  expect(document.querySelector(".tl-security-cue")).toBeNull()
  // Check the dismissed row again before its late notification.
  document.querySelector(".bog")!.textContent = "Hertz: Confirm your sign-in"
  document
    .querySelector(".bog")!
    .setAttribute("data-legacy-last-message-id", "ccc")
  await vi.advanceTimersByTimeAsync(500)
  // A late tab notification respects the first saved disposition.
  listeners[0](
    {
      type: "THREADLY_SECURITY_ACKED",
      key: "b".repeat(64),
      state: "recognised"
    },
    { id: "extension" }
  )
  expect(
    document.querySelector(".tl-left")!.getAttribute("aria-label")
  ).toContain("High priority")
})
