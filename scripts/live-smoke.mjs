/** Opt-in, read/generation-only acceptance against your running EC2 tunnel.
 * Uses an existing private laptop login session. No OAuth bypass on the server:
 * the real JWT is verified by the backend. Does not test the Google UI handshake.
 * Never invokes approval, send, event creation, or mailbox sync endpoints.
 */
import assert from "node:assert/strict"
import { mkdtemp, readFile, rm, stat } from "node:fs/promises"
import { homedir, tmpdir } from "node:os"
import path from "node:path"
import { chromium } from "@playwright/test"

if (process.env.THREADLY_LIVE_TEST !== "1")
  throw new Error(
    "Opt in with THREADLY_LIVE_TEST=1; this reads selected Gmail messages and invokes the configured model."
  )
const origin = "http://127.0.0.1:8000"
const sessionPath = path.join(homedir(), ".threadly-staging/session.json")
const info = await stat(sessionPath)
assert.equal(info.mode & 0o777, 0o600, "Private session must have mode 600")
const session = JSON.parse(await readFile(sessionPath, "utf8"))
const profile = await mkdtemp(path.join(tmpdir(), "threadly-live-extension-"))
let browser
try {
  const extension = path.resolve("build/chrome-mv3-prod")
  browser = await chromium.launchPersistentContext(profile, {
    channel: "chromium",
    headless: true,
    args: [
      `--disable-extensions-except=${extension}`,
      `--load-extension=${extension}`
    ]
  })
  console.log("Live test browser started")
  browser.setDefaultTimeout(45000)
  const worker =
    browser.serviceWorkers()[0] || (await browser.waitForEvent("serviceworker"))
  await worker.evaluate(
    async ({ origin, session }) => {
      await chrome.storage.local.set({ backendOrigin: origin })
      await chrome.storage.session.set({
        threadlySession: { ...session, origin }
      })
    },
    { origin, session }
  )
  console.log("Private session loaded in isolated test profile")
  const page = await browser.newPage()
  await page.setViewportSize({ width: 440, height: 960 })
  await page.goto(
    `chrome-extension://${worker.url().split("/")[2]}/sidepanel.html`
  )
  const { expect } = await import("@playwright/test")
  const error = () => page.locator("[role=alert]").allTextContents()
  await expect(
    page.getByRole("button", { name: "Search mail", exact: true })
  ).toBeVisible({ timeout: 30000 })
  console.log("Authenticated panel ready")
  await page.getByRole("button", { name: "Search mail", exact: true }).click()
  await page
    .getByLabel("Search text")
    .fill(process.env.THREADLY_MAIL_QUERY || "GYG")
  await page.getByLabel("Past days").fill("30")
  await page.getByRole("button", { name: "Search", exact: true }).click()
  await expect(page.locator(".history-row").first()).toBeVisible({
    timeout: 45000
  })
  await page.locator(".history-row").first().click()
  await expect(
    page.getByRole("button", { name: "Use as target" }).first()
  ).toBeVisible({ timeout: 45000 })
  await page.getByRole("button", { name: "Use as target" }).first().click()
  console.log("PASS: live bounded Gmail search and owned thread selection")
  for (const [mode, prompt, selector] of [
    ["summarise", "Summarise this thread.", '[aria-label="summary result"]'],
    [
      "ask",
      "What is the order total in this email?",
      '[aria-label="answer result"]'
    ],
    [
      "reply",
      "Draft a short thank-you reply for the order confirmation.",
      'textarea[aria-label="Message"]'
    ]
  ]) {
    await page.getByLabel("Request type").selectOption(mode)
    await page.getByLabel("Your request").fill(prompt)
    await page.getByRole("button", { name: "Submit", exact: true }).click()
    try {
      await expect(page.locator(selector).last()).toBeVisible({
        timeout: 180000
      })
    } catch {
      console.log("VISIBLE_ERRORS:", JSON.stringify(await error()))
      throw new Error(`Live ${mode} did not render a completed result`)
    }
    console.log(`PASS: live ${mode} task and rendered result`)
  }
  await page.getByLabel("Request type").selectOption("compose")
  await page.getByLabel("Recipient", { exact: true }).fill("alex@example.test")
  await page
    .getByLabel("Your request")
    .fill(
      "Write Alex a short email thanking them for the project update. Say I will review it tomorrow."
    )
  await page.getByRole("button", { name: "Submit", exact: true }).click()
  await expect(page.locator('textarea[aria-label="Message"]')).toHaveCount(2, {
    timeout: 180000
  })
  console.log("PASS: live independent compose while a thread is selected")
  await page.getByRole("button", { name: "Settings", exact: true }).click()
  await page
    .getByRole("button", { name: "Load calendars and preferences" })
    .click()
  await expect(page.getByLabel("Timezone", { exact: true })).toBeVisible({
    timeout: 45000
  })
  console.log("PASS: live Calendar list; no preferences changed")
  console.log(
    "LIVE_FRONTEND_SMOKE_PASSED; no messages sent, events created, or mailbox imported"
  )
} finally {
  await browser?.close()
  await rm(profile, { recursive: true, force: true })
}
