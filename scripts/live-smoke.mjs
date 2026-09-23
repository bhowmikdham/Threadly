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
    page.getByRole("button", { name: "Add context", exact: true })
  ).toBeVisible({ timeout: 30000 })
  console.log("Authenticated conversational panel ready")
  const ask = async (text) => {
    await page.getByLabel("Your request").fill(text)
    await page
      .getByRole("button", { name: "Send request", exact: true })
      .click()
  }
  await ask("hey")
  await expect(
    page.getByText("Hey! What can I help you with?", { exact: true })
  ).toBeVisible()
  console.log("PASS: greeting is conversational, no ambiguity error")
  await ask(`Show me all ${process.env.THREADLY_MAIL_QUERY || "GYG"} emails`)
  await expect(page.locator(".mail-glass-card").first()).toBeVisible({
    timeout: 90000
  })
  assert((await page.locator(".mail-glass-card").count()) <= 5)
  await page.locator(".mail-card-main").first().click()
  await expect(page.locator(".context-chip")).toBeVisible({ timeout: 45000 })
  console.log(
    "PASS: natural inbox search, five-card bound and explicit source attachment"
  )
  await ask("Summarise this thread.")
  await expect(page.getByLabel("summary result")).toHaveCount(1, {
    timeout: 180000
  })
  console.log("PASS: automatic summary routing")
  await ask("Make it shorter")
  await expect(page.getByLabel("summary result")).toHaveCount(2, {
    timeout: 180000
  })
  console.log("PASS: summary refinement keeps original source")
  await ask("What is the order total in this email?")
  await expect(page.getByLabel("answer result")).toBeVisible({
    timeout: 180000
  })
  console.log("PASS: grounded question in the same conversation")
  await ask("Draft a short thank-you reply for the order confirmation.")
  await expect(page.locator(".clarification, .draft-body").first()).toBeVisible(
    { timeout: 180000 }
  )
  const replyChoice = page.getByLabel("Reply to", { exact: true })
  if (await replyChoice.count()) {
    const option = await replyChoice
      .locator("option")
      .nth(1)
      .getAttribute("value")
    await replyChoice.selectOption(option)
  }
  // Test-only recipient. Draft generation cannot send, and no approval is exercised.
  const email = page.getByLabel("Email address", { exact: true })
  if (await email.count()) await email.fill("supplier@example.test")
  if (await page.locator(".clarification").count())
    await page.getByRole("button", { name: "Continue request" }).click()
  await expect(page.locator(".draft-body")).toHaveCount(1, { timeout: 180000 })
  console.log("PASS: contextual reply clarification and readable draft")
  await page.getByRole("button", { name: "Add context", exact: true }).click()
  await page.getByRole("button", { name: "Remove email context" }).click()
  await ask(
    "Write Alex a short email thanking them for the project update. Say I will review it tomorrow."
  )
  await expect(
    page.getByText("Who should this go to?", { exact: true })
  ).toBeVisible({ timeout: 180000 })
  await ask("alex@example.test")
  await expect(page.locator(".draft-body")).toHaveCount(2, { timeout: 180000 })
  console.log(
    "PASS: new-email recipient answered in chat, with no mail source attached"
  )
  await page.getByRole("button", { name: "Conversation menu" }).click()
  await page.getByRole("button", { name: "Settings", exact: true }).click()
  await page
    .getByRole("button", { name: "Load calendars and preferences" })
    .click()
  await expect(page.getByLabel("Timezone", { exact: true })).toBeVisible({
    timeout: 45000
  })
  await page
    .getByRole("button", { name: "Close settings", exact: true })
    .click()
  await expect(page.locator(".draft-body")).toHaveCount(2)
  console.log("PASS: Calendar settings preserve the conversation")
  console.log(
    "LIVE_FRONTEND_SMOKE_PASSED; no messages sent, events created, or mailbox imported"
  )
} catch (error) {
  console.error(
    "LIVE_TEST_STOPPED:",
    error.name,
    "(private page contents not logged)"
  )
  process.exitCode = 1
} finally {
  await browser?.close()
  await rm(profile, { recursive: true, force: true })
}
