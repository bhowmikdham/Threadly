import { mkdtemp, readFile, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import {
  chromium,
  expect,
  test,
  type BrowserContext,
  type Page
} from "@playwright/test"

let context: BrowserContext, page: Page, setup: Page, profile: string
const fixture = `<!doctype html><html><head><style>body{font:14px Arial;margin:0}table{width:100%;table-layout:fixed;border-collapse:collapse}td{padding:6px;overflow:hidden}.xT{display:flex;align-items:center;overflow:hidden;white-space:nowrap}.y6{min-width:0;overflow:hidden}.y2{color:#687078}.sender{width:110px}.when{width:60px}tr{border-bottom:1px solid #ddd}button{font:inherit}</style></head><body><div aria-label="Google Account: Synthetic (tester@example.test)">Synthetic Gmail fixture</div><div role="main"><table><tr class="zA" tabindex="0"><td style="width:24px"><input type="checkbox" aria-label="Select synthetic message"></td><td style="width:24px"><button aria-label="Star synthetic message">☆</button></td><td class="sender">Example sender</td><td><div class="xS"><div class="xT"><div class="y6"><span class="bog" data-legacy-thread-id="aaa" data-legacy-last-message-id="aaa0">Security alert</span></div><span class="y2"> — Synthetic preview</span></div></div></td><td class="when">12:00</td></tr></table></div><script>document.querySelector('tr').addEventListener('click',e=>{if(!e.target.closest('button,input'))document.body.dataset.opened='yes'})</script></body></html>`
async function cache(id: string, age = 0, metadata = true) {
  await setup.evaluate(
    async ({ id, age, metadata }) => {
      const received = new Date(Date.now() - age).toISOString()
      await chrome.storage.session.set({
        [`classification:19:aaa:${id}:UTC`]: {
          schema_version: "email-classification-with-action.v1",
          thread_id: "aaa",
          status: "classified",
          labels: {
            needs_reply: false,
            priority: "High",
            category: "it",
            action: "review"
          },
          source: "live_gmail",
          persisted: false,
          source_message_ids: [id],
          valid_until: new Date(Date.now() + 3600000).toISOString(),
          ...(metadata
            ? {
                latest_incoming: {
                  message_id: id,
                  received_at: received,
                  timestamp_source: "gmail.internalDate"
                }
              }
            : {})
        }
      })
    },
    { id, age, metadata }
  )
}
async function reuse(id: string, subject = "Security alert") {
  await page.locator(".bog").evaluate(
    (el, { id, subject }) => {
      el.textContent = subject
      el.setAttribute("data-legacy-last-message-id", id)
    },
    { id, subject }
  )
}
test.beforeAll(async () => {
  profile = await mkdtemp(path.join(tmpdir(), "threadly-security-"))
  const extension = path.resolve(
    process.env.THREADLY_E2E_EXTENSION_PATH || "build/chrome-mv3-prod"
  )
  await readFile(path.join(extension, "manifest.json"), "utf8")
  context = await chromium.launchPersistentContext(profile, {
    channel: "chromium",
    headless: true,
    timezoneId: "UTC",
    args: [
      `--disable-extensions-except=${extension}`,
      `--load-extension=${extension}`
    ]
  })
  const worker =
    context.serviceWorkers()[0] || (await context.waitForEvent("serviceworker"))
  setup = await context.newPage()
  await setup.goto(
    `chrome-extension://${new URL(worker.url()).host}/sidepanel.html`
  )
  await setup.evaluate(async () => {
    const status = await chrome.runtime.sendMessage({
      channel: "threadly",
      type: "STATUS"
    })
    await chrome.storage.local.set({
      threadlySession: {
        jwt:
          "header." +
          btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })) +
          ".signature",
        refresh_token: "synthetic-refresh",
        user: { id: 19, email: "tester@example.test", name: "Synthetic" },
        origin: status.data.origin
      },
      threadlyInboxMarks: { enabled: true, words: true }
    })
  })
  await context.route("https://api.threadly.au/**", (route) => route.abort())
  await context.route("https://mail.google.com/**", (route) =>
    route.fulfill({ contentType: "text/html", body: fixture })
  )
  page = await context.newPage()
  await cache("aaa0", 31 * 86400000)
  await page.goto("https://mail.google.com/mail/u/0/#inbox")
  await expect(page.locator(".tl-right[aria-label]")).toHaveCount(1)
})
test.afterAll(async () => {
  await context?.close()
  await rm(profile, { recursive: true, force: true })
})

test("only new provider-dated messages prompt; recognition persists and row reuse is safe", async () => {
  await expect(page.locator(".tl-security-cue")).toHaveCount(0)
  await cache("aaa1")
  await reuse("aaa1")
  const cue = page.getByRole("group", {
    name: "Possible security alert; sender not verified"
  })
  await expect(cue).toBeVisible()
  await expect(cue).toContainText("Do you recognise this security alert?")
  await expect(cue).toContainText("sender not verified")
  const animation = await page
    .locator("tr")
    .evaluate((el) => getComputedStyle(el).animationIterationCount)
  expect(animation).toBe("2")
  for (const width of [1440, 640]) {
    await page.setViewportSize({ width, height: 720 })
    await expect(
      cue.getByRole("button", { name: "I recognise it" })
    ).toBeInViewport()
    await expect(
      cue.getByRole("button", { name: "Dismiss", exact: true })
    ).toBeInViewport()
    const box = await cue.boundingBox()
    expect(box.x + box.width).toBeLessThanOrEqual(width)
    await expect(page.locator(".bog")).toHaveText("Security alert")
    if (process.env.THREADLY_SECURITY_SCREENSHOT_DIR)
      await page.screenshot({
        path: path.join(
          process.env.THREADLY_SECURITY_SCREENSHOT_DIR,
          `security-${width}.png`
        )
      })
  }
  await page.emulateMedia({ reducedMotion: "reduce" })
  expect(
    await page
      .locator("tr")
      .evaluate((el) => getComputedStyle(el).animationName)
  ).toBe("none")
  await cue.getByRole("button", { name: "I recognise it" }).focus()
  await page.keyboard.press("Enter")
  await expect(cue).toHaveCount(0)
  expect(await page.locator("body").getAttribute("data-opened")).toBeNull()
  // Restore original fixture identity to the acknowledged message after navigation.
  await page.reload()
  await reuse("aaa1")
  await expect(page.locator(".tl-right[aria-label]")).toHaveCount(1)
  await expect(cue).toHaveCount(0)
  await cache("aaa2")
  await reuse("aaa2")
  await expect(cue).toBeVisible()
  await cue.getByRole("button", { name: "Dismiss", exact: true }).click()
  await expect(cue).toHaveCount(0)
  const state = await setup.evaluate(
    async () =>
      (await chrome.storage.local.get("threadlySecurityAwarenessV1"))
        .threadlySecurityAwarenessV1
  )
  expect(state.acknowledgements).toHaveLength(2)
  for (const forbidden of [
    "tester@example.test",
    "Security alert",
    "aaa1",
    "aaa2",
    "subject",
    "body",
    "sender",
    "snippet"
  ])
    expect(JSON.stringify(state)).not.toContain(forbidden)
  await cache("aaa3", 0, false)
  await reuse("aaa3")
  await expect(page.locator(".tl-right[aria-label]")).toHaveCount(1)
  await expect(cue).toHaveCount(0)
  await cache("aaa4")
  await reuse("aaa4", "Security alert newsletter")
  await expect(cue).toHaveCount(0)
  await cache("aaa5")
  await reuse("aaa5", "<img src=x onerror=alert(1)>")
  await expect(cue).toHaveCount(0)
  await expect(page.locator(".bog img")).toHaveCount(0)
})

test("words/icons, account isolation, disabled marks and unavailable storage fail closed", async () => {
  await cache("bbb1")
  await reuse("bbb1")
  await expect(page.locator(".tl-security-cue")).toBeVisible()
  await setup.evaluate(() =>
    chrome.storage.local.set({
      threadlyInboxMarks: { enabled: true, words: false }
    })
  )
  await expect(page.locator(".tl-security-cue")).toBeVisible()
  await expect(page.locator(".tl-right .tl-word")).toBeHidden()
  await page
    .locator("[aria-label^='Google Account']")
    .evaluate((el) =>
      el.setAttribute(
        "aria-label",
        "Google Account: Other (other@example.test)"
      )
    )
  await page.evaluate(() =>
    window.dispatchEvent(new HashChangeEvent("hashchange"))
  )
  await expect(page.locator(".tl-security-cue")).toHaveCount(0)
  await page
    .locator("[aria-label^='Google Account']")
    .evaluate((el) =>
      el.setAttribute(
        "aria-label",
        "Google Account: Synthetic (tester@example.test)"
      )
    )
  await page.evaluate(() =>
    window.dispatchEvent(new HashChangeEvent("hashchange"))
  )
  await expect(page.locator(".tl-security-cue")).toBeVisible()
  await setup.evaluate(() =>
    chrome.storage.local.set({
      threadlyInboxMarks: { enabled: false, words: false }
    })
  )
  await expect(page.locator(".tl-security-cue")).toHaveCount(0)
  await setup.evaluate(() =>
    chrome.storage.local.set({
      threadlySecurityAwarenessV1: { version: 1 },
      threadlyInboxMarks: { enabled: true, words: true }
    })
  )
  await expect(page.locator(".tl-right[aria-label]")).toHaveCount(1)
  await expect(page.locator(".tl-security-cue")).toHaveCount(0)
  await page.getByRole("checkbox").check()
  await expect(page.getByRole("checkbox")).toBeChecked()
  await page.getByRole("button", { name: "Star synthetic message" }).click()
  expect(await page.locator("body").getAttribute("data-opened")).toBeNull()
})
