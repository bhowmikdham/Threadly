import { mkdtemp, readFile, rm } from "node:fs/promises"
import { createServer, type Server } from "node:http"
import { tmpdir } from "node:os"
import path from "node:path"
import {
  chromium,
  expect,
  test,
  type BrowserContext,
  type Page
} from "@playwright/test"

// The built extension on a synthetic Gmail inbox whose rows copy real Gmail's
// structure (tr.zA > .xT > .y6 + .y2, data-legacy-thread-id), against a local
// stand-in for POST /threads/{id}/classification. No real mail or account.
const user = { id: 1, email: "tester@example.test", name: "Tester" }
const rows = [
  { thread: "a1", subject: "Master services agreement" },
  { thread: "a2", subject: "Invoice failed" },
  { thread: "a3", subject: "Ambiguous note" },
  { thread: "a4", subject: "Team offsite" }
]
const labels: Record<string, any> = {
  a1: {
    needs_reply: true,
    priority: "High",
    category: "legal_contracts",
    action: "review"
  },
  a2: {
    needs_reply: false,
    priority: "Low",
    category: "finance_payments",
    action: "no_action"
  },
  a4: {
    needs_reply: true,
    priority: "Medium",
    category: "projects",
    action: "attend"
  }
}
const inbox = (
  email: string
) => `<!doctype html><html><head><title>Inbox</title></head>
<body style="background:#f8fafd;margin:0">
<a aria-label="Google Account: Tester (${email})">account</a>
<div role="main"><table><tbody>
${rows
  .map(
    (r) => `<tr class="zA"><td class="xY a4W" style="display:flex;width:700px">
  <div class="xS" style="flex:1 1 auto;overflow:hidden"><div class="xT" style="display:flex;align-items:center;overflow:hidden">
    <div class="y6" style="display:flex"><span class="bog"><span data-thread-id="#thread-f:1" data-legacy-thread-id="${r.thread}" data-legacy-last-message-id="${r.thread}0">${r.subject}</span></span></div>
    <span class="y2" style="flex:1 1 0;overflow:hidden"> — snippet</span>
  </div></div></td></tr>`
  )
  .join("")}
</tbody></table></div></body></html>`

let server: Server,
  origin: string,
  context: BrowserContext,
  page: Page,
  profile: string
const calls: { thread: string; body: any; at: number }[] = []
const summaries: string[] = []
let active = 0,
  maxActive = 0,
  busyOnce = true
test.beforeAll(async () => {
  server = createServer((req, res) => {
    const match = req.url?.match(/^\/threads\/([a-f0-9]+)\/classification$/)
    if (!match || req.method !== "POST") {
      res.writeHead(404, { "Content-Type": "application/json" })
      return res.end(
        JSON.stringify({ error: { code: "not_found", message: "" } })
      )
    }
    let raw = ""
    req.on("data", (c) => (raw += c))
    req.on("end", () => {
      const thread = match[1]
      calls.push({ thread, body: JSON.parse(raw), at: Date.now() })
      active += 1
      maxActive = Math.max(maxActive, active)
      setTimeout(() => {
        active -= 1
        res.setHeader("Content-Type", "application/json")
        if (thread === "a2" && busyOnce) {
          busyOnce = false
          res.writeHead(429, { "Retry-After": "5" })
          return res.end(
            JSON.stringify({
              error: { code: "classification_busy", message: "Busy" }
            })
          )
        }
        res.end(
          JSON.stringify({
            schema_version: "email-classification-with-action.v1",
            thread_id: thread,
            source_message_ids: [thread + "0"],
            source_fingerprint: "fixture",
            status: labels[thread] ? "classified" : "needs_review",
            labels: labels[thread] || null,
            evidence: null,
            reason_codes: labels[thread] ? [] : ["insufficient_context"],
            evaluated_at: new Date().toISOString(),
            valid_until: new Date(Date.now() + 300000).toISOString(),
            time_zone: "Australia/Melbourne",
            release_id: "fixture",
            source: "live_gmail",
            coverage: "cleaned_text_only",
            persisted: false
          })
        )
      }, 300)
    })
  })
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r))
  origin = `http://127.0.0.1:${(server.address() as any).port}`
  profile = await mkdtemp(path.join(tmpdir(), "threadly-badges-test-"))
  const extension = path.resolve("build/chrome-mv3-prod")
  JSON.parse(await readFile(path.join(extension, "manifest.json"), "utf8"))
  context = await chromium.launchPersistentContext(profile, {
    channel: "chromium",
    headless: true,
    timezoneId: "Australia/Melbourne",
    args: [
      `--disable-extensions-except=${extension}`,
      `--load-extension=${extension}`
    ]
  })
  const worker =
    context
      .serviceWorkers()
      .find((w) => w.url().startsWith("chrome-extension://")) ||
    (await context.waitForEvent("serviceworker"))
  const id = new URL(worker.url()).host
  const setup = await context.newPage()
  await setup.goto(`chrome-extension://${id}/sidepanel.html`)
  await expect
    .poll(() => setup.evaluate(() => typeof chrome?.storage?.local?.set))
    .toBe("function")
  const jwt =
    "header." +
    Buffer.from(
      JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })
    ).toString("base64url") +
    ".signature"
  await setup.evaluate(
    async ({ origin, jwt, user }) => {
      await chrome.storage.local.set({ backendOrigin: origin })
      await chrome.storage.local.set({
        threadlySession: { jwt, refresh_token: "refresh", user, origin }
      })
    },
    { origin, jwt, user }
  )
  await setup.close()
  page = await context.newPage()
  page.on("console", (m) => {
    if (m.text().startsWith("Threadly badges:")) summaries.push(m.text())
  })
})
test.afterAll(async () => {
  await context?.close()
  await new Promise((r) => server.close(r))
  await rm(profile, { recursive: true, force: true })
})

test("inbox rows get priority, reply and category badges from the classification service", async () => {
  await context.route("https://mail.google.com/**", (route) =>
    route.fulfill({ contentType: "text/html", body: inbox(user.email) })
  )
  await page.goto("https://mail.google.com/mail/u/0/#inbox")
  const row = (thread: string) =>
    page
      .locator("tr.zA")
      .filter({ has: page.locator(`[data-legacy-thread-id="${thread}"]`) })

  // High priority, needs a reply, legal; slots sit before the subject and after the snippet.
  const left = row("a1").locator(".tl-left")
  await expect(left).toHaveAttribute(
    "aria-label",
    "High priority · reply needed · Review"
  )
  await expect(left.locator(".tl-high svg")).toBeVisible()
  await expect(left.locator(".tl-reply svg")).toBeVisible()
  await expect(row("a1").locator(".tl-right")).toHaveAttribute(
    "aria-label",
    "Legal"
  )
  expect(
    await row("a1")
      .locator(".xT")
      .evaluate((line) => [...line.children].map((c) => c.className))
  ).toEqual(["tl-slot tl-left", "y6", "y2", "tl-slot tl-right"])

  // Projects has its own icon; Attend is spelled out in the tooltip.
  await expect(row("a4").locator(".tl-right")).toHaveAttribute(
    "aria-label",
    "Projects"
  )
  await expect(row("a4").locator(".tl-left")).toHaveAttribute(
    "aria-label",
    "Medium priority · reply needed · Attend"
  )

  // Needs review shows nothing rather than a guessed Low / Other.
  await expect.poll(() => calls.some((c) => c.thread === "a3")).toBe(true)
  await page.waitForTimeout(800)
  await expect(row("a3").locator(".tl-left svg")).toHaveCount(0)
  await expect(row("a3").locator(".tl-right svg")).toHaveCount(0)

  // A busy server is retried after its 5 second back-off; no reply shows a hairline.
  await expect(row("a2").locator(".tl-left")).toHaveAttribute(
    "aria-label",
    "Low priority · no reply needed",
    { timeout: 15000 }
  )
  await expect(row("a2").locator(".tl-hairline")).toHaveCount(1)
  const a2 = calls.filter((c) => c.thread === "a2")
  expect(a2).toHaveLength(2)
  expect(a2[1].at - a2[0].at).toBeGreaterThanOrEqual(4500)

  // Only thread IDs and the time zone leave the browser, never mail content.
  expect(
    calls.every(
      (c) => JSON.stringify(c.body) === '{"time_zone":"Australia/Melbourne"}'
    )
  ).toBe(true)
  expect(maxActive).toBeLessThanOrEqual(2)

  // Hover shows the Gmail-style tooltip.
  await row("a1").locator(".tl-right").hover()
  await expect(page.locator(".tl-tooltip")).toHaveText("Legal")

  // Results stay in memory: re-drawing rows doesn't ask the server again.
  const before = calls.length
  await page.evaluate(() =>
    document.querySelectorAll(".tl-slot").forEach((slot) => slot.remove())
  )
  await expect(row("a1").locator(".tl-left")).toHaveAttribute(
    "aria-label",
    "High priority · reply needed · Review"
  )
  expect(calls.length).toBe(before)

  // DevTools gets one summary of why rows do or don't have badges.
  await expect
    .poll(() => summaries.at(-1))
    .toBe("Threadly badges: 3 classified · 1 needs review")

  // Reopening Gmail reuses this browser session's results: no new calls.
  await page.reload()
  await expect(row("a1").locator(".tl-left")).toHaveAttribute(
    "aria-label",
    "High priority · reply needed · Review"
  )
  await page.waitForTimeout(1500)
  expect(calls.length).toBe(before)
})

test("a row that only flashes past while scrolling is not classified", async () => {
  const before = calls.length
  const fresh = await context.newPage()
  await fresh.setViewportSize({ width: 900, height: 80 })
  await context.unroute("https://mail.google.com/**")
  await context.route("https://mail.google.com/**", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: inbox(user.email)
        .replace(/a([1-4])/g, "b$1")
        .replace("<body", "<style>tr.zA{height:60px}</style><body")
    })
  )
  await fresh.goto("https://mail.google.com/mail/u/0/#inbox")
  // Scroll to the bottom straight away, then stay there.
  await fresh.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
  await fresh.waitForTimeout(2500)
  const asked = calls.slice(before).map((c) => c.thread)
  expect(asked).toContain("b4")
  expect(asked).not.toContain("b1")
  await fresh.close()
})

test("another Gmail account gets no badges and no classification calls", async () => {
  const before = calls.length
  const other = await context.newPage()
  await context.unroute("https://mail.google.com/**")
  await context.route("https://mail.google.com/**", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: inbox("someone-else@example.test")
    })
  )
  await other.goto("https://mail.google.com/mail/u/1/#inbox")
  await other.waitForTimeout(1500)
  await expect(other.locator(".tl-slot:visible")).toHaveCount(0)
  expect(calls.length).toBe(before)
  await other.close()
})
