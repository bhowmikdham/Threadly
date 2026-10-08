import { mkdtemp, rm } from "node:fs/promises"
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

// Cooldowns from the classification server (release/backend
// docs/classification/FRONTEND-RELIABILITY-HANDOFF.md): honour Retry-After,
// pause every Gmail tab, keep waiting rows pending, and only resume rows that
// are still on screen for the same account. Each test gets a fresh browser so
// one test's pause can't leak into the next. Synthetic data only.
const user = { id: 1, email: "tester@example.test", name: "Tester" }
const threads = ["c1", "c2", "c3", "c4"]
const inbox = (
  email: string
) => `<!doctype html><html><head><meta charset="utf-8"><title>Inbox</title></head>
<body style="background:#fff;margin:0">
<a aria-label="Google Account: Tester (${email})">account</a>
<div role="main"><table><tbody>
${threads
  .map(
    (
      t
    ) => `<tr class="zA" style="height:40px"><td class="xY a4W" style="display:flex;width:700px">
  <div class="xS" style="flex:1 1 auto;overflow:hidden"><div class="xT" style="display:flex;align-items:center;overflow:hidden">
    <div class="y6" style="display:flex"><span class="bog"><span data-legacy-thread-id="${t}" data-legacy-last-message-id="${t}0">Subject ${t}</span></span></div>
    <span class="y2" style="flex:1 1 0;overflow:hidden"> - snippet</span>
  </div></div></td></tr>`
  )
  .join("")}
</tbody></table></div></body></html>`

type Reply = { status: number; code?: string; retryAfter?: number }
let server: Server, origin: string
let calls: { thread: string; at: number }[] = []
// Decides each response; defaults to a successful classification.
let answer: (thread: string, n: number) => Reply = () => ({ status: 200 })
const classified = (thread: string) => ({
  schema_version: "email-classification-with-action.v1",
  thread_id: thread,
  source_message_ids: [thread + "0"],
  source_fingerprint: "fixture",
  status: "classified",
  labels: {
    needs_reply: false,
    priority: "Low",
    category: "other",
    action: "no_action"
  },
  evidence: null,
  reason_codes: [],
  evaluated_at: new Date().toISOString(),
  // The server's new one-hour validity.
  valid_until: new Date(Date.now() + 3600000).toISOString(),
  time_zone: "Australia/Melbourne",
  release_id: "fixture",
  source: "live_gmail",
  coverage: "cleaned_text_only",
  persisted: false
})
test.beforeAll(async () => {
  server = createServer((req, res) => {
    const match = req.url?.match(/^\/threads\/([a-f0-9]+)\/classification$/)
    res.setHeader("Content-Type", "application/json")
    if (!match) {
      res.writeHead(404)
      return res.end(
        JSON.stringify({ error: { code: "not_found", message: "" } })
      )
    }
    req.resume()
    req.on("end", () => {
      const thread = match[1]
      calls.push({ thread, at: Date.now() })
      const r = answer(thread, calls.filter((c) => c.thread === thread).length)
      setTimeout(() => {
        if (r.status === 200) return res.end(JSON.stringify(classified(thread)))
        res.writeHead(
          r.status,
          r.retryAfter ? { "Retry-After": String(r.retryAfter) } : {}
        )
        res.end(
          JSON.stringify({ error: { code: r.code, message: "", detail: null } })
        )
      }, 100)
    })
  })
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r))
  origin = `http://127.0.0.1:${(server.address() as any).port}`
})
test.afterAll(async () => {
  await new Promise((r) => server.close(r))
})

let context: BrowserContext, profile: string
async function launch(email = user.email) {
  calls = []
  profile = await mkdtemp(path.join(tmpdir(), "threadly-cooldown-test-"))
  const extension = path.resolve("build/chrome-mv3-prod")
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
  const setup = await context.newPage()
  await setup.goto(
    `chrome-extension://${new URL(worker.url()).host}/sidepanel.html`
  )
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
  await context.route("https://mail.google.com/**", (route) =>
    route.fulfill({ contentType: "text/html", body: inbox(email) })
  )
  return openInbox()
}
async function openInbox(): Promise<Page> {
  const page = await context.newPage()
  await page.goto("https://mail.google.com/mail/u/0/#inbox")
  return page
}
test.afterEach(async () => {
  await context?.close()
  await rm(profile, { recursive: true, force: true })
})
const badges = (page: Page) => page.locator(".tl-left .tl-priority")

test("a 60 second Retry-After pauses every Gmail tab and keeps rows pending", async () => {
  answer = () => ({ status: 429, code: "classification_busy", retryAfter: 60 })
  const page = await launch()
  // Two rows go out together (two at a time), are told to wait 60 s, and
  // nothing else is asked for while the pause lasts.
  await expect.poll(() => calls.length).toBeGreaterThan(0)
  await page.waitForTimeout(4000)
  expect(calls.length).toBeLessThanOrEqual(2)
  await expect(badges(page)).toHaveCount(0)
  // A second Gmail tab shares the pause instead of asking again.
  const before = calls.length
  const other = await openInbox()
  await other.waitForTimeout(3000)
  expect(calls.length).toBe(before)
  await expect(badges(other)).toHaveCount(0)
})

test("after a short pause only rows still on screen are classified, and kept for the hour", async () => {
  answer = (_thread, n) =>
    n === 1
      ? { status: 429, code: "classification_busy", retryAfter: 2 }
      : { status: 200 }
  const page = await launch()
  await expect.poll(() => calls.length).toBeGreaterThan(0)
  const first = calls[0].at
  // c4 leaves the screen during the pause, so it isn't asked for afterwards.
  await page.evaluate(() => {
    const row = document
      .querySelector('[data-legacy-thread-id="c4"]')!
      .closest("tr")!
    ;(row as HTMLElement).style.display = "none"
  })
  await expect(badges(page)).toHaveCount(3, { timeout: 15000 })
  const resumed = calls.filter((c) => c.at > first + 1500)
  expect(resumed.length).toBeGreaterThan(0)
  expect(resumed.every((c) => c.at >= first + 2000)).toBe(true)
  expect(calls.slice(2).map((c) => c.thread)).not.toContain("c4")
  // One-hour results are reused when Gmail is reopened. Only c4, back on
  // screen in the fresh page and never classified, is asked for.
  const before = calls.length
  await page.reload()
  await expect(badges(page)).toHaveCount(4, { timeout: 15000 })
  // (Its first call is told to wait too, so it may be asked twice.)
  expect([...new Set(calls.slice(before).map((c) => c.thread))]).toEqual(["c4"])
})

test("an exhausted Gmail quota waits out its long Retry-After, even after a reload", async () => {
  answer = () => ({
    status: 503,
    code: "gmail_quota_exceeded",
    retryAfter: 3600
  })
  const page = await launch()
  await expect.poll(() => calls.length).toBeGreaterThan(0)
  await page.waitForTimeout(3000)
  const before = calls.length
  expect(before).toBeLessThanOrEqual(2)
  await page.reload()
  await page.waitForTimeout(3000)
  expect(calls.length).toBe(before)
  await expect(badges(page)).toHaveCount(0)
})

test("AI-unavailable errors are retried a bounded number of times, then left blank", async () => {
  answer = (thread) =>
    thread === "c1"
      ? {
          status: 503,
          code: "classification_provider_unavailable",
          retryAfter: 1
        }
      : { status: 200 }
  const page = await launch()
  await expect(badges(page)).toHaveCount(3, { timeout: 20000 })
  await page.waitForTimeout(4000)
  expect(calls.filter((c) => c.thread === "c1")).toHaveLength(3)
  await expect(
    page.locator('tr:has([data-legacy-thread-id="c1"]) .tl-priority')
  ).toHaveCount(0)
})

test("switching Gmail account during a pause drops the old account's waiting rows", async () => {
  answer = () => ({ status: 429, code: "classification_busy", retryAfter: 2 })
  const page = await launch()
  await expect.poll(() => calls.length).toBeGreaterThan(0)
  const before = calls.length
  // The page now belongs to another account, which Threadly isn't connected to.
  await page.evaluate(() => {
    document
      .querySelector('[aria-label^="Google Account:"]')!
      .setAttribute(
        "aria-label",
        "Google Account: Other (someone-else@example.test)"
      )
    // Gmail re-renders when the account changes; that's what prompts a rescan.
    document.body.append(document.createElement("div"))
  })
  answer = () => ({ status: 200 })
  await page.waitForTimeout(5000)
  expect(calls.length).toBe(before)
  await expect(badges(page)).toHaveCount(0)
})
