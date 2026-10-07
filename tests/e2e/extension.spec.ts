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
    timezoneId: "Australia/Melbourne",
    locale: "en-AU",
    args: [
      `--disable-extensions-except=${extension}`,
      `--load-extension=${extension}`,
      // A synthetic microphone (a steady tone) for the voice orb.
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream"
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
      await chrome.storage.local.set({
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
test("availability cards accompany concise replies, recheck only latest, and fit narrow themes", async () => {
  await page.getByLabel("Your request").fill("am I free at 5pm tomorrow")
  await page.getByLabel("Your request").press("Enter")
  const cards = page.getByRole("region", { name: "Calendar availability" })
  const first = cards.first()
  await expect(
    first.getByRole("heading", { name: "Busy", exact: true })
  ).toBeVisible()
  await expect(
    page.getByText("No, you're busy tomorrow at 5 pm.", { exact: false })
  ).toBeVisible()
  await expect(
    first.getByText("Australia/Melbourne · Selected calendars")
  ).toBeVisible()
  await expect(first.getByText(/17:00|5:00 pm|5:00 PM/).first()).toBeVisible()
  await page.setViewportSize({ width: 343, height: 840 })
  await first.scrollIntoViewIfNeeded()
  await page.screenshot({ path: "test-results/availability-busy-light.png" })
  await first.getByRole("button", { name: "Check again" }).focus()
  await page.keyboard.press("Enter")
  await expect(
    cards.last().getByRole("heading", { name: "Free", exact: true })
  ).toBeVisible()
  await expect(
    first.getByRole("button", { name: "Check again" })
  ).toBeDisabled()
  await page.getByLabel("Your request").fill("am I free at 6pm tomorrow")
  await page.getByLabel("Your request").press("Enter")
  const last = cards.last()
  await expect(
    last.getByRole("heading", { name: "Availability uncertain" })
  ).toBeVisible()
  await expect(
    last.getByText(/Some selected calendars couldn't be checked/)
  ).toBeVisible()
  await page.setViewportSize({ width: 320, height: 840 })
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "Account menu" }).click()
  await page.getByRole("button", { name: "Switch to dark appearance" }).click()
  await page.getByRole("button", { name: "Close conversation menu" }).click()
  await last.scrollIntoViewIfNeeded()
  await page.screenshot({ path: "test-results/availability-partial-dark.png" })
  const box = await last.boundingBox()
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(320)
  expect(await last.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(
    true
  )
  expect(
    calls
      .filter((c) => c.path === "/assistant/conversation-turns")
      .map((c) => c.body.instruction)
  ).toEqual([
    "am I free at 5pm tomorrow",
    "Check that availability again",
    "am I free at 6pm tomorrow"
  ])
  expect(
    calls.some((c) => /approve|calendar-actions|events/.test(c.path))
  ).toBe(false)
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "Account menu" }).click()
  await page.getByRole("button", { name: "Switch to light appearance" }).click()
  await page.getByRole("button", { name: "Close conversation menu" }).click()
  await page.setViewportSize({ width: 420, height: 900 })
  await page.getByRole("button", { name: "New chat", exact: true }).click()
  calls.length = 0
})

test("Calendar approval menu and exact event card in the built extension", async () => {
  await expect(
    page.getByRole("button", { name: "Calendar approval: Ask for approval" })
  ).toBeVisible()
  await page.getByLabel("Your request").fill("Create Focus at 2pm tomorrow")
  await page.getByLabel("Your request").press("Enter")
  const card = page.getByRole("region", { name: "Calendar event" }).last()
  await expect(card.getByRole("heading", { name: "Focus" })).toBeVisible()
  await expect(card.getByRole("button", { name: "Create event" })).toBeVisible()
  await page.screenshot({ path: "/tmp/threadly-event-review.png" })
  await card.getByRole("button", { name: "Create event" }).click()
  await expect(card.getByText("Event created", { exact: true })).toBeVisible()
  await page
    .getByRole("button", { name: "Calendar approval: Ask for approval" })
    .click()
  await expect(
    page.getByRole("menuitemradio", { name: /Ask for approval/ })
  ).toHaveAttribute("aria-checked", "true")
  await page.screenshot({ path: "/tmp/threadly-calendar-approval-menu.png" })
  await page.getByRole("menuitemradio", { name: /Always allow/ }).click()
  await expect(
    page.getByRole("button", { name: "Calendar approval: Always allow" })
  ).toBeVisible()
  await page.getByLabel("Your request").fill("Create Focus at 2pm tomorrow")
  await page.getByLabel("Your request").press("Enter")
  await expect(
    page
      .getByRole("region", { name: "Calendar event" })
      .last()
      .getByText("Event created", { exact: true })
  ).toBeVisible()
  expect(
    calls.filter(
      (c) =>
        c.path.endsWith("/approve") && c.path.includes("/calendar-actions/")
    )
  ).toHaveLength(1)
  await page
    .getByRole("button", { name: "Calendar approval: Always allow" })
    .click()
  await page.getByRole("menuitemradio", { name: /Ask for approval/ }).click()
  await expect(
    page.getByRole("button", { name: "Calendar approval: Ask for approval" })
  ).toBeVisible()
  await page.getByRole("button", { name: "New chat", exact: true }).click()
  calls.length = 0
})

test("tool-limit recovery retains the booking and Ask approval before a later availability question", async () => {
  control.failConversationToolLimit = true
  await page
    .getByLabel("Your request")
    .fill("book 2 pm tmrw for doctors appointment")
  await page.getByLabel("Your request").press("Enter")
  await expect(
    page.getByText(
      "Threadly couldn't finish this request. Retry response to continue with the same details."
    )
  ).toBeVisible()
  await expect(
    page.getByText(/Start a new conversation to continue/)
  ).toHaveCount(0)
  const issued = calls.find(
    (c) => c.path === "/assistant/conversation-turns"
  )!.body
  control.failConversationToolLimit = false
  await page
    .getByRole("button", { name: "Retry response", exact: true })
    .click()
  const card = page.getByRole("region", { name: "Calendar event" }).last()
  await expect(
    card.getByRole("heading", { name: "doctors appointment" })
  ).toBeVisible()
  await expect(
    card.getByRole("button", { name: "Create event", exact: true })
  ).toBeVisible()
  await expect(card.getByText("Event created", { exact: true })).toHaveCount(0)
  const turnCalls = calls.filter(
    (c) => c.path === "/assistant/conversation-turns"
  )
  expect(turnCalls).toHaveLength(2)
  expect(turnCalls[1].body).toEqual(issued)
  expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(false)
  await page.getByLabel("Your request").fill("am i free at 2 pm tmrw?")
  await page.getByLabel("Your request").press("Enter")
  await expect(
    page.getByRole("button", { name: "Review calendars" })
  ).toBeVisible()
  const next = calls
    .filter((c) => c.path === "/assistant/conversation-turns")
    .at(-1)!.body
  expect(next).toMatchObject({
    conversation_id: issued.conversation_id,
    expected_version: 1,
    instruction: "am i free at 2 pm tmrw?"
  })
  expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(false)
  await page.getByRole("button", { name: "New chat", exact: true }).click()
  calls.length = 0
})

test("an older stranded chat can be safely cancelled before the exact availability follow-up", async () => {
  const id = "00000000-0000-4000-8000-000000000010"
  const requestId = "00000000-0000-4000-8000-000000000011"
  control.strandedConversation = { id, requestId, version: 0, busy: true }
  await page.evaluate(async (id) => {
    await chrome.storage.session.set({
      "threadlyConversation:1": { id, version: 0 }
    })
  }, id)
  await page.reload()
  const recovery = page.getByRole("region", { name: "Unfinished response" })
  await expect(recovery).toBeVisible()
  await expect(page.getByLabel("Your request")).toBeDisabled()
  await expect(
    recovery.getByRole("button", { name: "Cancel unfinished request" })
  ).toHaveCount(0)
  await recovery
    .getByRole("button", { name: "Recover unfinished response" })
    .click()
  await expect(
    recovery.getByText("The response is still being prepared. Try shortly.")
  ).toBeVisible()
  await expect(page.getByLabel("Your request")).toBeDisabled()
  control.strandedConversation.busy = false
  await recovery
    .getByRole("button", { name: "Recover unfinished response" })
    .click()
  await expect(
    recovery.getByRole("button", { name: "Cancel unfinished request" })
  ).toBeVisible()
  expect(
    calls.filter(
      (c) => c.path.endsWith("/recover") && c.body.operation === "cancel"
    )
  ).toHaveLength(0)
  await recovery
    .getByRole("button", { name: "Cancel unfinished request" })
    .click()
  await expect(recovery).toHaveCount(0)
  await expect(
    page.getByText(
      "The unfinished request was cancelled. You can continue this chat."
    )
  ).toBeVisible()
  await expect(page.locator(".user-message")).toHaveCount(0)
  await page.getByLabel("Your request").fill("am i free at 2 pm tmrw?")
  await page.getByLabel("Your request").press("Enter")
  await expect(
    page.getByRole("button", { name: "Review calendars" })
  ).toBeVisible()
  const turn = calls
    .filter((c) => c.path === "/assistant/conversation-turns")
    .at(-1)!
  expect(turn.body).toMatchObject({
    conversation_id: id,
    expected_version: 1,
    instruction: "am i free at 2 pm tmrw?"
  })
  expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(false)
  await page.getByRole("button", { name: "New chat", exact: true }).click()
  control.strandedConversation = undefined
  calls.length = 0
})

test("recovering a saved response does not fabricate a user message or cancel existing work", async () => {
  const id = "00000000-0000-4000-8000-000000000020"
  control.strandedConversation = {
    id,
    requestId: "00000000-0000-4000-8000-000000000021",
    version: 0,
    saved: true
  }
  await page.evaluate(async (id) => {
    await chrome.storage.session.set({
      "threadlyConversation:1": { id, version: 0 }
    })
  }, id)
  await page.reload()
  await page
    .getByRole("button", { name: "Recover unfinished response" })
    .click()
  await expect(
    page.getByText("Your saved response is available again.")
  ).toBeVisible()
  await expect(page.locator(".user-message")).toHaveCount(0)
  await expect(page.getByLabel("Your request")).toBeEnabled()
  expect(
    calls
      .filter((c) => c.path.endsWith("/recover"))
      .map((c) => c.body.operation)
  ).toEqual(["recover"])
  expect(
    calls.some(
      (c) =>
        c.path.endsWith("/approve") ||
        c.path === "/assistant/conversation-turns"
    )
  ).toBe(false)
  await page.getByRole("button", { name: "New chat", exact: true }).click()
  control.strandedConversation = undefined
  calls.length = 0
})

test("clickable calendars retain a multi-turn Meeting and Ask approval through exact selection retry", async () => {
  await page.getByLabel("Your request").fill("could you create Meeting at 4 pm")
  await page.getByLabel("Your request").press("Enter")
  await expect(
    page.getByText("What day should I use for Meeting at 4 pm?")
  ).toBeVisible()
  await page.getByLabel("Your request").fill("tomorrow")
  await page.getByLabel("Your request").press("Enter")
  const choices = page
    .getByRole("region", { name: "Choose an event calendar" })
    .last()
  await expect(choices.getByRole("button")).toHaveCount(2)
  await expect(choices.getByRole("button", { name: "Holidays" })).toHaveCount(0)
  await page.setViewportSize({ width: 320, height: 740 })
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth
    )
  ).toBe(true)
  await page.screenshot({
    animations: "disabled",
    path: path.join("test-results", "calendar-choices-light.png")
  })
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "Account menu" }).click()
  await page.getByRole("button", { name: "Switch to dark appearance" }).click()
  await page.getByRole("button", { name: "Close conversation menu" }).click()
  await expect(
    choices.getByRole("button", { name: "Personal calendar" })
  ).toBeEnabled()
  await page.screenshot({
    animations: "disabled",
    path: path.join("test-results", "calendar-choices-dark.png")
  })
  await page.reload()
  await expect(
    choices.getByRole("button", { name: "Personal calendar" })
  ).toBeEnabled()
  control.failCalendarChoice = true
  await choices.getByRole("button", { name: "Personal calendar" }).focus()
  await page.keyboard.press("Enter")
  await expect(
    page.getByRole("button", { name: "Retry response", exact: true })
  ).toBeVisible()
  await expect(
    choices.getByRole("button", { name: "Personal calendar" })
  ).toBeDisabled()
  control.failCalendarChoice = false
  await page
    .getByRole("button", { name: "Retry response", exact: true })
    .click()
  const event = page.getByRole("region", { name: "Calendar event" }).last()
  await expect(event.getByRole("heading", { name: "Meeting" })).toBeVisible()
  await expect(event.getByText(/16:00|4:00/)).toBeVisible()
  await expect(
    event.getByRole("button", { name: "Create event", exact: true })
  ).toBeVisible()
  await expect(event.getByText("Event created", { exact: true })).toHaveCount(0)
  const selections = calls.filter((call) =>
    call.path.endsWith("/calendar-choice")
  )
  expect(selections).toHaveLength(2)
  expect(selections[1].body).toEqual(selections[0].body)
  expect(selections[0].body).toEqual({
    request_id: expect.any(String),
    expected_version: 2,
    choice_id: "00000000-0000-4000-8000-000000000101"
  })
  expect(calls.some((call) => call.path.endsWith("/approve"))).toBe(false)
  await page.getByRole("button", { name: "New chat", exact: true }).click()
  await expect(choices).toHaveCount(0)
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "Account menu" }).click()
  await page.getByRole("button", { name: "Switch to light appearance" }).click()
  await page.getByRole("button", { name: "Close conversation menu" }).click()
  await page.setViewportSize({ width: 420, height: 900 })
  calls.length = 0
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
    page.locator(".chat-response strong", { hasText: "matching emails" })
  ).toBeVisible()
  await expect(
    page.getByText("All mail · Times in Australia/Melbourne")
  ).toBeVisible()
  await expect(page.locator(".mail-card-date").first()).toHaveText(
    /23 Sept 2026.*1:00 pm.*AEST/i
  )
  await expect(page.locator(".mail-card-date").first()).toHaveAttribute(
    "datetime",
    "2026-09-23T03:00:00Z"
  )
  await expect(page.locator(".mail-card-snippet").first()).toHaveText(
    "Thanks for your order. Your receipt and pickup details are inside."
  )
  await expect(
    page
      .locator(".exchange")
      .filter({ has: page.locator(".inbox-results") })
      .getByText("Sources", { exact: true })
  ).toHaveCount(0)
  await expect(
    page.getByText("Showing 5 of 10 emails from this search.")
  ).toBeVisible()
  await page.setViewportSize({ width: 390, height: 900 })
  await page.locator(".inbox-results").evaluate((element) => {
    element.closest(".exchange")?.scrollIntoView({ block: "start" })
  })
  await page.screenshot({
    animations: "disabled",
    path: path.join("test-results", "inbox-local-time-390.png")
  })
  await page.setViewportSize({ width: 420, height: 900 })
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
  await expect(page.locator(".gmail-draft-message textarea")).toBeVisible()
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
  // A reply card is only From, To and Message; it goes into Gmail with Insert.
  const editor = page.getByLabel("Email draft").last()
  await expect(editor.getByLabel("Subject", { exact: true })).toHaveCount(0)
  await expect(
    editor.getByRole("button", { name: "Create draft", exact: true })
  ).toHaveCount(0)
  await editor
    .getByLabel("Message", { exact: true })
    .fill("My exact edited body")
  // No Gmail reply box is open in this harness, so Insert waits for one.
  await expect(
    editor.getByRole("button", { name: "Insert", exact: true })
  ).toBeDisabled()
  await expect(editor.getByText(/Click Reply in Gmail/)).toBeVisible()
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
  await expect(
    page.getByRole("button", { name: "Insert", exact: true }).last()
  ).toBeVisible()
  expect(
    calls.filter((c) => c.path === "/assistant/gmail-drafts")
  ).toHaveLength(0)
  expect(
    calls.some((c) => c.path.includes("/assistant/gmail-drafts/task/"))
  ).toBe(true)
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
  await expect(page.locator(".gmail-draft-message textarea")).toBeVisible()
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
test("voice mode is a spoken back-and-forth that also lands in the chat", async () => {
  await page.evaluate(() => {
    class FakeRecognition {
      onresult: any
      onend: any
      onerror: any
      start() {
        ;(window as any).fakeRecognizer = this
      }
      stop() {}
      abort() {}
    }
    ;(window as any).SpeechRecognition = FakeRecognition
    ;(window as any).webkitSpeechRecognition = FakeRecognition
    ;(window as any).spoken = []
    // Speaking takes a moment, so the "Speaking…" state can be seen.
    Object.defineProperty(window, "speechSynthesis", {
      configurable: true,
      value: {
        getVoices: () => [],
        cancel() {},
        speak(u: any) {
          ;(window as any).spoken.push(u.text)
          u.onstart?.()
          setTimeout(() => u.onend?.(), 1500)
        }
      }
    })
  })
  await page.getByRole("button", { name: "Talk to Threadly" }).click()
  const dialog = page.getByRole("dialog", { name: "Voice conversation" })
  await expect(dialog).toBeVisible()
  expect(
    await page.evaluate(
      () =>
        !!document
          .querySelector<HTMLCanvasElement>("canvas.voice-orb")
          ?.getContext("webgl")
    )
  ).toBe(true)
  await expect(dialog.getByRole("status")).toHaveText("Listening…")
  await page.waitForTimeout(600)
  await page.screenshot({
    path: path.join("test-results", "voice-listening.png")
  })
  await page.evaluate(() =>
    (window as any).fakeRecognizer.onresult({
      results: [Object.assign([{ transcript: "hey" }], { isFinal: true })]
    })
  )
  // What was said is not shown on the orb screen.
  await expect(dialog.getByText("hey", { exact: true })).toHaveCount(0)
  await expect(dialog.getByRole("status")).toHaveText("Speaking…")
  await page.screenshot({
    path: path.join("test-results", "voice-speaking.png")
  })
  await expect
    .poll(() => page.evaluate(() => (window as any).spoken))
    .toEqual(["Let me check that.", "Hey! What can I help you with?"])
  await expect(dialog).not.toHaveClass(/\bdocked\b/)
  await expect(dialog.getByRole("status")).toHaveText("Listening…")
  await page.getByRole("button", { name: "Close voice conversation" }).click()
  await expect(dialog).toHaveCount(0)
  await expect(
    page.getByText("Hey! What can I help you with?", { exact: true }).last()
  ).toBeVisible()
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
test("connected connectors open capability details and the update page is bundled", async () => {
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "Manage Google Calendar" }).click()
  await page.getByText("What Threadly can do", { exact: true }).click()
  await expect(
    page.getByRole("list", { name: "Skills", exact: true })
  ).toBeVisible()
  await expect(
    page.getByText("Find availability", { exact: true })
  ).toBeVisible()
  await page.screenshot({
    path: path.join("test-results", "connector-capabilities.png"),
    fullPage: true
  })
  await page.getByRole("button", { name: "Back to chat", exact: true }).click()
  const update = await context.newPage()
  await update.goto(new URL("tabs/updated.html", page.url()).href)
  await expect(
    update.getByRole("heading", { name: "Threadly was updated" })
  ).toBeVisible()
  await update
    .getByRole("link", { name: "Privacy", exact: true })
    .scrollIntoViewIfNeeded()
  await expect(
    update.getByRole("link", { name: "Privacy", exact: true })
  ).toBeVisible()
  await update.locator(".update-page").evaluate((element) => {
    element.scrollTop = 0
  })
  await update.screenshot({
    path: path.join("test-results", "updated-page.png"),
    fullPage: true
  })
  await update.close()
})

test("Calendar recovery is clear, compact and usable at 320px", async () => {
  control.calendarSelection = ["primary", "holiday"]
  await page.reload()
  await page.setViewportSize({ width: 380, height: 820 })
  await page.getByLabel("Your request").fill("am i free on the tuesday")
  await page.getByLabel("Your request").press("Enter")
  await page
    .getByRole("button", { name: "Review calendars", exact: true })
    .click()
  await expect(
    page.getByText("Some calendars couldn't be checked")
  ).toBeVisible()
  await expect(
    page.getByLabel("Holidays in India", { exact: true })
  ).toBeChecked()
  await page.getByText("What Threadly can do", { exact: true }).click()
  await expect(
    page.getByRole("list", { name: "Skills" }).locator("li")
  ).toHaveCount(4)
  await page.getByText("What Threadly can do", { exact: true }).click()
  await expect(page.getByText("Threadly server", { exact: true })).toHaveCount(
    0
  )
  await page.screenshot({
    path: path.join("test-results", "calendar-connection-light.png")
  })
  await page.setViewportSize({ width: 320, height: 740 })
  expect(
    await page
      .locator(".settings")
      .evaluate((el) => el.scrollWidth <= el.clientWidth)
  ).toBe(true)
  await page.screenshot({
    path: path.join("test-results", "calendar-connection-narrow.png")
  })
  await page.getByLabel("Holidays in India", { exact: true }).uncheck()
  await page.getByRole("button", { name: "Save changes" }).click()
  await expect(page.getByText("All selected calendars checked")).toBeVisible()
  await page.getByRole("button", { name: "Back to chat", exact: true }).click()
  control.calendarPreferencesStale = true
  await page.reload()
  await page
    .getByRole("button", { name: "Review calendars", exact: true })
    .last()
    .click()
  await expect(page.locator(".calendar-notice")).toBeVisible()
  await page.getByRole("button", { name: "Save changes" }).click()
  await expect(page.getByText("All selected calendars checked")).toBeVisible()
  // The real theme toggle is covered in the suite; set the same saved preference
  // here so both versions of this screen get a screenshot without extra menu state.
  await page.evaluate(() => chrome.storage.local.set({ darkMode: true }))
  await page.reload()
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "Manage Google Calendar" }).click()
  await expect(page.getByText("All selected calendars checked")).toBeVisible()
  await page.setViewportSize({ width: 380, height: 820 })
  await page.screenshot({
    path: path.join("test-results", "calendar-connection-dark.png")
  })
  await page.getByRole("button", { name: "Back to chat", exact: true }).click()
  await page.evaluate(() => chrome.storage.local.set({ darkMode: false }))
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
  await page.getByRole("button", { name: "Manage Google Calendar" }).click()
  await expect(page.getByText("All selected calendars checked")).toBeVisible()
  await page.locator(".working-hours > summary").click()
  expect(
    await page
      .locator(".threadly")
      .evaluate((element) => element.scrollWidth <= element.clientWidth)
  ).toBe(true)
  await expect(page.getByLabel("Timezone", { exact: true })).toHaveValue(
    "Australia/Melbourne"
  )
  await page.getByLabel("Timezone", { exact: true }).fill("UTC")
  await page.getByRole("button", { name: "Save changes" }).click()
  await expect(page.getByText(/Calendar settings saved/)).toBeVisible()
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
      await chrome.storage.local.set({
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
    await page.evaluate(async () => chrome.storage.local.get("threadlySession"))
  ).toEqual({})
  control.failLogout = false
})

test("logout never sends a retained token to a stale server origin", async () => {
  const priorLogoutCalls = calls.filter((c) => c.path === "/auth/logout").length
  await page.evaluate(
    async ({ user }) => {
      await chrome.storage.local.set({
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
    await page.evaluate(async () => chrome.storage.local.get("threadlySession"))
  ).toEqual({})
})

test("disconnect preserves session on failure and clears it on confirmed success", async () => {
  const jwt =
    "header." +
    Buffer.from(
      JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })
    ).toString("base64url") +
    ".signature"
  await page.evaluate(
    async ({ origin, jwt, user }) =>
      chrome.storage.local.set({ threadlySession: { origin, jwt, user } }),
    { origin, jwt, user }
  )
  control.failDisconnect = true
  const send = () =>
    page.evaluate(() =>
      chrome.runtime.sendMessage({
        channel: "threadly",
        type: "DISCONNECT_GOOGLE"
      })
    )
  expect((await send()).ok).toBe(false)
  expect(
    await page.evaluate(
      async () =>
        !!(await chrome.storage.local.get("threadlySession")).threadlySession
    )
  ).toBe(true)
  control.failDisconnect = false
  expect(await send()).toMatchObject({ ok: true, data: { connected: false } })
  expect(
    await page.evaluate(async () => chrome.storage.local.get("threadlySession"))
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
      await chrome.storage.local.set({
        threadlySession: { jwt, user, origin }
      })
    },
    { origin, jwt: nearExpiryJwt, user }
  )
  const worker = context.serviceWorkers()[0]
  await worker.evaluate(() => {
    const originalSet = chrome.storage.local.set.bind(chrome.storage.local)
    const originalRemove = chrome.storage.local.remove.bind(
      chrome.storage.local
    )
    const state = {
      waiting: false,
      removeStarted: false,
      release: null as (() => void) | null
    }
    ;(globalThis as any).__threadlySessionRace = state
    chrome.storage.local.set = async (items) => {
      if (items.threadlySession?.jwt?.endsWith(".refreshed")) {
        state.waiting = true
        await new Promise<void>((resolve) => {
          state.release = resolve
        })
      }
      return originalSet(items)
    }
    chrome.storage.local.remove = async (keys) => {
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
    await page.evaluate(async () => chrome.storage.local.get("threadlySession"))
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
