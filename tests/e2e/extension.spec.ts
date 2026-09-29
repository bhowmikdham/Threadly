import { mkdtemp, readFile, rm } from "node:fs/promises"
import type { Server } from "node:http"
import { tmpdir } from "node:os"
import path from "node:path"
import {
  chromium,
  expect,
  test,
  type BrowserContext,
  type Page
} from "@playwright/test"

import {
  createMockBackend,
  target,
  user,
  type Call,
  type MockControl
} from "./mock-backend"

let server: Server,
  context: BrowserContext,
  page: Page,
  profile: string,
  origin: string
test.describe.configure({ mode: "serial" })
let calls: Call[] = []
let control: MockControl
const contractFailures: unknown[] = []
test.beforeAll(async () => {
  ;({ server, calls, control } = createMockBackend(
    (label, actual, expected) => {
      try {
        expect(actual, label).toEqual(expected)
      } catch (error) {
        contractFailures.push(error)
        throw error
      }
    }
  ))
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r))
  origin = `http://127.0.0.1:${(server.address() as any).port}`
  profile = await mkdtemp(path.join(tmpdir(), "threadly-extension-test-"))
  const extension = path.resolve("build/chrome-mv3-prod")
  const manifest = JSON.parse(
    await readFile(path.join(extension, "manifest.json"), "utf8")
  )
  expect(
    manifest.permissions,
    "The built extension must request the storage permission"
  ).toContain("storage")
  context = await chromium.launchPersistentContext(profile, {
    channel: "chromium",
    headless: true,
    args: [
      `--disable-extensions-except=${extension}`,
      `--load-extension=${extension}`
    ]
  })
  const worker =
    context
      .serviceWorkers()
      .find((candidate) => candidate.url().startsWith("chrome-extension://")) ||
    (await context.waitForEvent("serviceworker", {
      predicate: (candidate) =>
        candidate.url().startsWith("chrome-extension://")
    }))
  const extensionId = new URL(worker.url()).host
  page = await context.newPage()
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto(`chrome-extension://${extensionId}/sidepanel.html`)
  await expect
    .poll(
      () =>
        page.evaluate(() => ({
          local: typeof globalThis.chrome?.storage?.local?.set,
          session: typeof globalThis.chrome?.storage?.session?.set
        })),
      {
        message: `Extension storage APIs unavailable in ${page.url()} (worker: ${worker.url()}, permissions: ${JSON.stringify(manifest.permissions)})`,
        timeout: 10000
      }
    )
    .toEqual({ local: "function", session: "function" })
  const jwt =
    "header." +
    Buffer.from(
      JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })
    ).toString("base64url") +
    ".signature"
  await page.evaluate(
    async ({ origin, jwt, user }) => {
      await chrome.storage.local.set({ backendOrigin: origin })
      await chrome.storage.session.set({
        threadlySession: { jwt, user, origin }
      })
    },
    { origin, jwt, user }
  )
  await page.reload()
})
test.afterEach(() => {
  expect(contractFailures).toEqual([])
})
test.afterAll(async () => {
  await context?.close()
  await new Promise<void>((r) => server?.close(() => r()))
  if (profile) await rm(profile, { recursive: true, force: true })
})
test("real extension bridge: selected summary, answer and edited reply without send", async () => {
  await page.getByLabel("Your request").fill("hey")
  await page.getByLabel("Your request").press("Enter")
  await expect(
    page.getByText("Hey! What can I help you with?", { exact: true })
  ).toBeVisible()
  await expect(page.getByText(/Ambiguous request/)).toHaveCount(0)
  await page.getByLabel("Your request").fill("Show me all GYG emails")
  await page.getByLabel("Your request").press("Enter")
  await expect(page.locator(".mail-glass-card")).toHaveCount(5)
  await expect(
    page.getByText("Showing 5 of 10 emails from this search.")
  ).toBeVisible()
  const turnsBeforeReveal = calls.filter(
    (call) => call.path === "/assistant/conversation-turns"
  ).length
  await page.getByRole("button", { name: "Show 5 more results" }).click()
  await expect(page.locator(".mail-glass-card")).toHaveCount(10)
  expect(
    calls.filter((call) => call.path === "/assistant/conversation-turns").length
  ).toBe(turnsBeforeReveal)
  await expect(page.getByLabel("Search text")).toHaveCount(0)
  await expect(page.getByLabel("Flight from JFK to MEL")).toBeVisible()
  await page.screenshot({
    animations: "disabled",
    path: path.join("test-results", "inbox-glass-cards.png")
  })
  await page.emulateMedia({ reducedMotion: "reduce" })
  await expect(page.locator(".flight-plane")).toHaveCSS("display", "none")
  await expect(page.locator(".flight-plane-static")).toBeVisible()
  await page.setViewportSize({ width: 320, height: 740 })
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth
    )
  ).toBe(true)
  await page.screenshot({
    path: path.join("test-results", "inbox-cards-narrow.png")
  })
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "Account menu" }).click()
  await page.getByRole("button", { name: "Switch to dark appearance" }).click()
  await page.getByRole("button", { name: "Close conversation menu" }).click()
  await page.screenshot({
    path: path.join("test-results", "inbox-cards-dark.png")
  })
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "Account menu" }).click()
  await page.getByRole("button", { name: "Switch to light appearance" }).click()
  await page.getByRole("button", { name: "Close conversation menu" }).click()
  await page.setViewportSize({ width: 420, height: 900 })
  await page.emulateMedia({ reducedMotion: "no-preference" })
  await page.getByRole("button", { name: "Show more emails" }).click()
  await expect(page.locator(".mail-glass-card")).toHaveCount(11)
  await page
    .getByRole("button", { name: "Use email: GYG order 7833", exact: true })
    .click()
  await expect(
    page.getByRole("button", { name: "Use email: GYG order 7833", exact: true })
  ).toHaveAttribute("aria-pressed", "true")
  await page
    .getByRole("button", { name: "Use email: Test receipt", exact: true })
    .click()
  await expect(page.locator(".context-chip")).toBeVisible()
  await expect(page.getByLabel("Request type")).toHaveCount(0)
  await page.screenshot({
    animations: "disabled",
    path: path.join("test-results", "conversation-start.png")
  })
  await page.getByLabel("Your request").fill("Summarise this thread.")
  await page.getByLabel("Your request").press("Enter")
  await expect(
    page.getByText("Order 7842 totals $18.60. Pickup is at 6:20 PM.")
  ).toBeVisible()
  await page.getByLabel("Your request").fill("Make it shorter")
  await page.getByLabel("Your request").press("Enter")
  await expect(page.getByLabel("summary result")).toHaveCount(2)
  const refinement = calls
    .filter((c) => c.path === "/assistant/conversation-turns")
    .at(-1).body
  expect(refinement.context_snapshot_id).toBe("ctx-1")
  expect(refinement.active_task_id).toBeTruthy()
  expect(refinement.instruction).toContain("Make it shorter")
  await page.screenshot({
    animations: "disabled",
    path: path.join("test-results", "conversation-summary.png")
  })
  await page.getByLabel("Your request").fill("How much did I pay?")
  await page.getByRole("button", { name: "Send request", exact: true }).click()
  await expect(page.getByText("$18.60", { exact: true })).toBeVisible()
  await page.getByLabel("Your request").fill("Draft a reply to this thread.")
  await page.getByRole("button", { name: "Send request", exact: true }).click()
  // The attached email already fixes the reply target and sender, so the
  // request continues without asking.
  await expect(page.locator(".draft-body")).toBeVisible()
  await expect(
    page.getByText("Which message are you replying to?")
  ).toHaveCount(0)
  expect(
    calls.filter((c) => c.path.endsWith("/inputs")).at(-1)?.body.answer
  ).toEqual({ reply_message_id: target, recipients: ["supplier@example.test"] })
  // No edit form: the reply is changed by asking in the chat.
  await expect(page.getByRole("button", { name: "Edit draft" })).toHaveCount(0)
  await expect(page.getByLabel("Your request")).toHaveAttribute(
    "placeholder",
    "Edit the reply or ask a question…"
  )
  await page.getByLabel("Your request").fill("The total should be $9.80")
  await page.getByRole("button", { name: "Send request", exact: true }).click()
  await expect(
    page.getByText("Thank you for the update. The total should be $9.80.")
  ).toBeVisible()
  await page
    .getByRole("button", { name: "Review outgoing email" })
    .last()
    .click()
  await expect(page.getByText("Exact outgoing email")).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Approve and send" })
  ).toHaveCount(0)
  expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(false)
  expect(calls.some((c) => c.path.startsWith("/sync"))).toBe(false)
  await page.reload()
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  // Recent chats leaves out the chat that is already open.
  await page.getByRole("button", { name: "Recent chats", exact: true }).click()
  await expect(
    page.getByRole("button", { name: /The total should be/ })
  ).toHaveCount(0)
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "New conversation" }).click()
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "Recent chats", exact: true }).click()
  await page.getByRole("button", { name: /The total should be/ }).click()
  await expect(page.getByText("Exact outgoing email")).toBeVisible()
  expect(calls.filter((c) => c.path.endsWith("/actions"))).toHaveLength(1)
  expect(calls.some((c) => c.path === "/assistant/actions/action-1")).toBe(true)
  await page.screenshot({
    animations: "disabled",
    path: path.join("test-results", "extension-preview.png"),
    fullPage: true
  })
})
test("multi-step proposal requires explicit confirmation and scheduling renders real slots", async () => {
  await page.getByLabel("Your request").fill("Find a meeting time tomorrow.")
  await page.getByRole("button", { name: "Send request", exact: true }).click()
  await expect(page.getByText("Here’s what I’ll do")).toBeVisible()
  expect(calls.filter((c) => c.path.endsWith("/confirm"))).toHaveLength(0)
  await page.getByRole("button", { name: "Continue with these steps" }).click()
  await expect(page.getByText("Found 1 option.")).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Select and recheck" })
  ).toBeVisible()
})
test("a new-email recipient is answered in chat and the panel fits narrow light and dark views", async () => {
  await page.getByRole("button", { name: "New chat", exact: true }).click()
  await page
    .getByLabel("Your request")
    .fill("Write an email thanking Alex for the update.")
  await page.getByLabel("Your request").press("Enter")
  await expect(
    page.getByText("Who should this go to?", { exact: true })
  ).toBeVisible()
  const count = calls.filter(
    (c) => c.path === "/assistant/conversation-turns"
  ).length
  await page.getByLabel("Your request").fill("alex@example.test")
  await page.getByLabel("Your request").press("Enter")
  await expect(page.locator(".draft-body")).toBeVisible()
  expect(
    calls.filter((c) => c.path === "/assistant/conversation-turns")
  ).toHaveLength(count + 1)
  expect(
    calls.filter((c) => c.path === "/assistant/conversation-turns").at(-1).body
      .instruction
  ).toEqual("alex@example.test")
  await page.setViewportSize({ width: 320, height: 640 })
  await expect(page.getByRole("button", { name: "Send request" })).toBeVisible()
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )
  ).toBe(true)
  await page.screenshot({
    animations: "disabled",
    path: path.join("test-results", "conversation-narrow-light.png")
  })
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "Account menu" }).click()
  await page.getByRole("button", { name: "Switch to dark appearance" }).click()
  await page
    .getByRole("button", { name: "Close conversation menu", exact: true })
    .click()
  await page.screenshot({
    animations: "disabled",
    path: path.join("test-results", "conversation-narrow-dark.png")
  })
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "Account menu" }).click()
  await page.getByRole("button", { name: "Switch to light appearance" }).click()
  await page
    .getByRole("button", { name: "Close conversation menu", exact: true })
    .click()
  await page.setViewportSize({ width: 420, height: 900 })
})
test("an attached email can be detached directly from the composer", async () => {
  await page.getByRole("button", { name: "New chat", exact: true }).click()
  await page.getByLabel("Your request").fill("Show me all GYG emails")
  await page.getByLabel("Your request").press("Enter")
  const email = page.getByRole("button", {
    name: "Use email: Test receipt",
    exact: true
  })
  await expect(email).toBeVisible()
  await email.click()
  await expect(
    page.getByRole("button", { name: "Detach email context" })
  ).toBeVisible()
  await page.setViewportSize({ width: 320, height: 640 })
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )
  ).toBe(true)
  await page.screenshot({
    animations: "disabled",
    path: path.join("test-results", "attached-email-narrow.png")
  })
  await page.getByRole("button", { name: "Detach email context" }).click()
  await expect(page.locator(".context-chip")).toHaveCount(0)
  await expect(email).toHaveAttribute("aria-pressed", "false")

  await page.getByLabel("Your request").fill("hey")
  await page.getByLabel("Your request").press("Enter")
  await expect(
    page.getByText("Hey! What can I help you with?", { exact: true })
  ).toBeVisible()
  const turn = calls
    .filter((c) => c.path === "/assistant/conversation-turns")
    .at(-1)
  expect(turn?.body).toHaveProperty("context_snapshot_id", null)
  await page.setViewportSize({ width: 420, height: 900 })
})
test("a missing connector offers Connect in the menu, not only in Settings", async () => {
  control.calendarConnected = false
  try {
    await page.reload()
    await page
      .getByRole("button", { name: "Conversation menu", exact: true })
      .click()
    await expect(
      page.getByRole("button", { name: "Connect Google Calendar" })
    ).toBeVisible()
    await expect(
      page.getByRole("button", { name: "Connect Gmail" })
    ).toHaveCount(0)
    await expect(page.locator(".connector-status.is-on")).toHaveText(
      "Connected"
    )
    await page.screenshot({
      path: path.join("test-results", "connectors-connect.png")
    })
    await page.getByRole("button", { name: "Close conversation menu" }).click()
  } finally {
    control.calendarConnected = true
    await page.reload()
  }
})
test("settings use real capability and versioned preference contracts; history survives panel reload", async () => {
  await page.reload()
  await page.setViewportSize({ width: 360, height: 900 })
  expect(
    await page
      .locator(".threadly")
      .evaluate((element) => element.scrollWidth <= element.clientWidth)
  ).toBe(true)
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "Recent chats", exact: true }).click()
  await expect(
    page
      .getByRole("heading", { name: "Recent chats" })
      .or(page.getByText("Recent chats", { exact: true }))
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: /Test receipt/ }).first()
  ).toBeVisible()
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "Account menu" }).click()
  await page.getByRole("button", { name: "Settings", exact: true }).click()
  await page
    .getByRole("button", { name: "Load calendars and preferences" })
    .click()
  await page
    .getByRole("heading", { name: "See upcoming events" })
    .scrollIntoViewIfNeeded()
  expect(
    await page
      .locator(".threadly")
      .evaluate((element) => element.scrollWidth <= element.clientWidth)
  ).toBe(true)
  await expect(page.getByLabel("Timezone", { exact: true })).toHaveValue(
    "Australia/Melbourne"
  )
  await page
    .getByRole("button", { name: "Save scheduling preferences" })
    .click()
  await expect(page.getByText(/Scheduling preferences saved/)).toBeVisible()
  expect(
    calls.find((c) => c.path === "/calendar/preferences" && c.body)?.body
      .expected_version
  ).toBe(1)
  await page.getByRole("button", { name: "Back to chat", exact: true }).click()
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  const genericAuthCall = await page.evaluate(() =>
    chrome.runtime.sendMessage({
      channel: "threadly",
      type: "API",
      path: "/auth/logout",
      method: "POST"
    })
  )
  expect(genericAuthCall.ok).toBe(false)
  await page.getByRole("button", { name: "Account menu" }).click()
  await page.getByRole("button", { name: "Sign out", exact: true }).click()
  await expect(
    page.getByRole("button", { name: "Sign in with Google" })
  ).toBeVisible()
  expect(calls.filter((c) => c.path === "/auth/logout")).toEqual([
    {
      path: "/auth/logout",
      method: "POST",
      body: undefined,
      authorized: true
    }
  ])
  await page.setViewportSize({ width: 420, height: 900 })
})

test("server logout failure clears the browser session and warns about remaining sessions", async () => {
  control.failLogout = true
  const jwt =
    "header." +
    Buffer.from(
      JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })
    ).toString("base64url") +
    ".signature"
  await page.evaluate(
    async ({ origin, jwt, user }) => {
      await chrome.storage.session.set({
        threadlySession: { jwt, user, origin }
      })
    },
    { origin, jwt, user }
  )
  await page.reload()
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "Account menu" }).click()
  await page.getByRole("button", { name: "Sign out", exact: true }).click()
  await expect(
    page.getByRole("button", { name: "Sign in with Google" })
  ).toBeVisible()
  await expect(page.getByRole("alert")).toContainText(
    "could not confirm server sign-out"
  )
  expect(
    await page.evaluate(async () =>
      chrome.storage.session.get("threadlySession")
    )
  ).toEqual({})
  control.failLogout = false
})

test("logout never sends a retained token to a stale server origin", async () => {
  const priorLogoutCalls = calls.filter((c) => c.path === "/auth/logout").length
  await page.evaluate(
    async ({ user }) => {
      await chrome.storage.session.set({
        threadlySession: {
          jwt: "retained-token",
          user,
          origin: "https://unexpected.example.test"
        }
      })
    },
    { user }
  )
  const response = await page.evaluate(() =>
    chrome.runtime.sendMessage({ channel: "threadly", type: "LOGOUT" })
  )
  expect(response).toEqual({ ok: true, data: { serverRevoked: false } })
  expect(calls.filter((c) => c.path === "/auth/logout")).toHaveLength(
    priorLogoutCalls
  )
  expect(
    await page.evaluate(async () =>
      chrome.storage.session.get("threadlySession")
    )
  ).toEqual({})
})

test("concurrent token refresh cannot restore a signed-out session", async () => {
  const nearExpiryJwt =
    "header." +
    Buffer.from(
      JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 30 })
    ).toString("base64url") +
    ".signature"
  await page.evaluate(
    async ({ origin, jwt, user }) => {
      await chrome.storage.session.set({
        threadlySession: { jwt, user, origin }
      })
    },
    { origin, jwt: nearExpiryJwt, user }
  )
  const worker = context.serviceWorkers()[0]
  await worker.evaluate(() => {
    const originalSet = chrome.storage.session.set.bind(chrome.storage.session)
    const originalRemove = chrome.storage.session.remove.bind(
      chrome.storage.session
    )
    const state = {
      waiting: false,
      removeStarted: false,
      release: null as (() => void) | null
    }
    ;(globalThis as any).__threadlySessionRace = state
    chrome.storage.session.set = async (items) => {
      if (items.threadlySession?.jwt?.endsWith(".refreshed")) {
        state.waiting = true
        await new Promise<void>((resolve) => {
          state.release = resolve
        })
      }
      return originalSet(items)
    }
    chrome.storage.session.remove = async (keys) => {
      state.removeStarted = true
      return originalRemove(keys)
    }
  })
  const pendingRequest = page.evaluate(() =>
    chrome.runtime.sendMessage({
      channel: "threadly",
      type: "API",
      path: "/assistant/capabilities",
      method: "GET"
    })
  )
  await expect
    .poll(() =>
      worker.evaluate(() => (globalThis as any).__threadlySessionRace.waiting)
    )
    .toBe(true)
  const pendingLogout = page.evaluate(() =>
    chrome.runtime.sendMessage({ channel: "threadly", type: "LOGOUT" })
  )
  // While the refresh write is held, logout must wait for it before removing.
  await page.waitForTimeout(100)
  expect(
    await worker.evaluate(
      () => (globalThis as any).__threadlySessionRace.removeStarted
    )
  ).toBe(false)
  await worker.evaluate(() =>
    (globalThis as any).__threadlySessionRace.release()
  )
  await Promise.all([pendingRequest, pendingLogout])
  expect(
    await page.evaluate(async () =>
      chrome.storage.session.get("threadlySession")
    )
  ).toEqual({})
})

test("disconnected backend gives actionable login recovery without opening Google", async () => {
  const pageCount = context.pages().length
  await page.getByRole("button", { name: "Sign in with Google" }).click()
  await expect(page.getByRole("alert")).toContainText(
    "Cannot reach the Threadly backend"
  )
  await expect(page.getByRole("alert")).toContainText(
    "Keep the EC2 connection terminal open"
  )
  await expect(page.getByRole("alert")).not.toContainText("Failed to fetch")
  await expect(
    page.getByRole("button", { name: "Sign in with Google" })
  ).toBeEnabled()
  expect(context.pages()).toHaveLength(pageCount)
})
