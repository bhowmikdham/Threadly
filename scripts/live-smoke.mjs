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
let stage = "starting the extension"
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
  await expect(
    page.getByRole("button", { name: "Add context", exact: true })
  ).toBeVisible({ timeout: 30000 })
  console.log("Authenticated conversational panel ready")
  const exchanges = page.locator(".exchange")
  const ask = async (text) => {
    const before = await exchanges.count()
    await page.getByLabel("Your request").fill(text)
    await page
      .getByRole("button", { name: "Send request", exact: true })
      .click()
    await expect(exchanges).toHaveCount(before + 1, { timeout: 10000 })
    return exchanges.nth(before)
  }
  const completed = async (
    entry,
    selector,
    timeout = 180000,
    allowClarification = false,
    ready = async () => true
  ) => {
    const output = entry.locator(selector).first()
    const deadline = Date.now() + timeout
    while (Date.now() < deadline) {
      // Fail promptly, but never inspect or print an alert's potentially private text.
      if (
        (await entry.locator('[role="alert"]').count()) ||
        (await page.locator('.global-error[role="alert"]').count())
      ) {
        const failure = new Error("The backend or extension reported an error")
        failure.name = "BackendOrExtensionError"
        throw failure
      }
      if (
        !allowClarification &&
        (await entry.locator(".clarification").isVisible())
      ) {
        const failure = new Error("The request needs unexpected clarification")
        failure.name = "UnexpectedClarification"
        throw failure
      }
      if (await output.isVisible()) {
        if (
          ((allowClarification &&
            (await entry.locator(".clarification").isVisible())) ||
            !(await entry.locator(".thinking, .task-status").count())) &&
          (await ready())
        )
          return output
      }
      await page.waitForTimeout(500)
    }
    const failure = new Error("The expected response did not finish in time")
    failure.name = "SmokeTimeout"
    throw failure
  }

  stage = "greeting"
  const greeting = await ask("hey")
  const greetingResponse = await completed(greeting, ".chat-response")
  assert((await greetingResponse.textContent())?.trim().length > 1)
  assert.equal(
    await greeting.locator(".inbox-results, .artifact, .proposal").count(),
    0
  )
  console.log("PASS: greeting is conversational, no ambiguity error")

  stage = "bounded inbox search"
  const search = await ask(
    `Find recent emails from or about ${process.env.THREADLY_MAIL_QUERY || "GYG"}. Show matching email cards.`
  )
  await completed(search, ".mail-glass-card", 180000)
  const firstPageSize = await search.locator(".mail-glass-card").count()
  assert(firstPageSize >= 1 && firstPageSize <= 5)
  await expect(search.locator(".inbox-scope")).toBeVisible()
  await search.locator(".mail-card-main").first().click()
  await expect(page.locator(".context-chip")).toBeVisible({ timeout: 45000 })
  await expect(search.locator(".mail-card-main").first()).toHaveAttribute(
    "aria-pressed",
    "true"
  )
  console.log(
    "PASS: natural inbox search, five-card bound and explicit source attachment"
  )

  stage = "selected-email follow-up"
  const followUp = await ask(
    "What is this selected email about? Give one concrete detail and cite it."
  )
  await completed(
    followUp,
    ".chat-response, .artifact.result-answer, .artifact.result-summary",
    180000,
    false,
    async () =>
      (await followUp
        .locator(".work-details blockquote, .artifact .source blockquote")
        .count()) > 0
  )
  console.log("PASS: follow-up uses the selected email and cites evidence")

  stage = "selected-email summary"
  const summary = await ask("Summarise this selected email briefly for me.")
  await completed(
    summary,
    ".artifact.result-summary, .chat-response",
    180000,
    false,
    async () =>
      (await summary.locator(".artifact.result-summary .prose").count()) > 0 ||
      ((await summary.locator(".chat-response").count()) > 0 &&
        (await summary.locator(".work-details blockquote").count()) > 0)
  )
  await expect(page.locator(".context-chip")).toBeVisible()
  console.log(
    "PASS: selected-email summary stays grounded in the pinned source"
  )

  stage = "new-email draft"
  await page.getByRole("button", { name: "New chat", exact: true }).click()
  await expect(page.locator(".context-chip")).toHaveCount(0)
  const draft = await ask(
    "Draft a new email to alex@example.test. Subject: Project update. Thank Alex for the project update and say I will review it tomorrow. Do not send it."
  )
  await completed(draft, ".draft-body, .clarification", 180000, true)
  if (await draft.locator(".clarification").count()) {
    const recipient = draft.getByLabel("Email address", { exact: true })
    assert.equal(
      await recipient.count(),
      1,
      "The draft needs a clarification outside this read-only smoke"
    )
    await recipient.fill("alex@example.test")
    await draft.getByRole("button", { name: "Continue request" }).click()
  }
  await completed(draft, ".draft-body")
  assert((await draft.locator(".draft-body").textContent())?.trim().length > 0)
  console.log("PASS: new-email draft is reviewable and no send was requested")

  stage = "Calendar read and chat continuity"
  await page.getByRole("button", { name: "Conversation menu" }).click()
  await page.getByRole("button", { name: "Settings", exact: true }).click()
  const settings = page.locator(".settings")
  const loadCalendars = settings.getByRole("button", {
    name: "Load calendars and preferences"
  })
  await loadCalendars.click()
  await expect(settings.getByLabel("Timezone", { exact: true })).toBeVisible({
    timeout: 45000
  })
  await expect(loadCalendars).toBeEnabled({ timeout: 45000 })
  assert.equal(await settings.locator('[role="status"]').count(), 0)
  await settings.getByRole("button", { name: "Back to chat" }).click()
  await expect(draft.locator(".draft-body")).toBeVisible()
  console.log("PASS: Calendar settings preserve the conversation")
  console.log(
    "LIVE_FRONTEND_SMOKE_PASSED; no messages sent, events created, or mailbox imported"
  )
} catch (error) {
  console.error(
    "LIVE_TEST_STOPPED:",
    stage,
    error.name,
    "(private page contents not logged)"
  )
  process.exitCode = 1
} finally {
  await browser?.close()
  await rm(profile, { recursive: true, force: true })
}
