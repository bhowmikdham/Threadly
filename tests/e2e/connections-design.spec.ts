// Real built extension, isolated Chromium and synthetic API data only.
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

import { createMockBackend, user, type MockControl } from "./mock-backend"

type ResponseOverride = { data: unknown; status?: number; wait?: Promise<void> }
let server: Server, context: BrowserContext, page: Page, profile: string
let control: MockControl
let overrides: Record<string, ResponseOverride> = {}
const capabilities = (connected = true) => ({
  capabilities: [
    ...["gmail_read", "calendar_read", "calendar_list"].map((id) => ({
      id,
      enabled: true,
      ready: connected,
      status: connected ? "ready" : "scope_missing"
    })),
    ...["calendar_events_read", "calendar_write", "gmail_send"].map((id) => ({
      id,
      enabled: true,
      ready: false,
      status: "scope_missing"
    }))
  ]
})

test.beforeAll(async () => {
  const backend = createMockBackend()
  control = backend.control
  // Overrides are fixture controls, never part of the production bridge.
  server = createServer((req, res) => {
    const override = overrides[req.url!.split("?")[0]]
    if (!override) {
      backend.server.emit("request", req, res)
      return
    }
    void Promise.resolve(override.wait).then(() => {
      res.writeHead(override.status || 200, {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*"
      })
      res.end(JSON.stringify(override.data))
    })
  })
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve))
  const origin = `http://127.0.0.1:${(server.address() as any).port}`
  profile = await mkdtemp(path.join(tmpdir(), "threadly-connections-design-"))
  const extension = path.resolve("build/chrome-mv3-prod")
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
      .find((item) => item.url().startsWith("chrome-extension://")) ||
    (await context.waitForEvent("serviceworker", {
      predicate: (item) => item.url().startsWith("chrome-extension://")
    }))
  page = await context.newPage()
  await page.goto(
    `chrome-extension://${new URL(worker.url()).host}/sidepanel.html`
  )
  // Extension-page APIs and the existing startup barrier must be ready before
  // seeding. A newly announced service worker may not expose storage APIs yet.
  await expect
    .poll(() =>
      page.evaluate(() => typeof globalThis.chrome?.storage?.local?.set)
    )
    .toBe("function")
  await page.evaluate(() =>
    chrome.runtime.sendMessage({ channel: "threadly", type: "STATUS" })
  )
  const jwt =
    "fixture." +
    Buffer.from(
      JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })
    ).toString("base64url") +
    ".local"
  await page.evaluate(
    async ({ origin, jwt, user }) => {
      await chrome.storage.local.set({
        backendOrigin: origin,
        threadlySession: { jwt, user, origin }
      })
    },
    { origin, jwt, user }
  )
  await page.reload()
})
test.beforeEach(async () => {
  overrides = { "/assistant/capabilities": { data: capabilities() } }
  control.calendarConnected = true
  control.calendarSelection = ["primary"]
  control.calendarPreferencesStale = false
  await page.evaluate(() => chrome.storage.local.set({ darkMode: false }))
  await page.setViewportSize({ width: 380, height: 900 })
  await page.reload()
})
test.afterAll(async () => {
  await context?.close()
  await new Promise<void>((resolve) => server?.close(() => resolve()))
  if (profile) await rm(profile, { recursive: true, force: true })
})
async function open(id: "Gmail" | "Google Calendar") {
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: `Manage ${id}`, exact: true }).click()
}
async function shot(name: string) {
  await page.locator(".settings").evaluate((el) => {
    el.scrollTop = 0
  })
  await page.screenshot({
    path: path.join("test-results", `connections-${name}.png`)
  })
}
async function fits() {
  expect(
    await page
      .locator(".settings")
      .evaluate((el) => el.scrollWidth <= el.clientWidth)
  ).toBe(true)
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth
    )
  ).toBe(true)
}

test("connected Gmail, keyboard disclosures, consent explanation and return navigation", async () => {
  await open("Gmail")
  await expect(
    page.getByRole("button", { name: "Reconnect Gmail" })
  ).toBeVisible()
  await expect(page.locator(".connector-detail .connector-status")).toHaveText(
    "Connected"
  )
  await shot("gmail")
  await page.getByRole("button", { name: "Back to chat" }).focus()
  await page.keyboard.press("Tab")
  await expect(
    page.getByRole("button", { name: "Connections", exact: true })
  ).toBeFocused()
  await page.keyboard.press("Tab")
  await expect(
    page.getByRole("button", { name: "Reconnect Gmail" })
  ).toBeFocused()
  await page.keyboard.press("Tab")
  const features = page.locator(".connector-features > summary")
  await expect(features).toBeFocused()
  await expect(features).toHaveCSS("outline-style", "solid")
  await expect(features).toHaveCSS("outline-width", "2px")
  await page.keyboard.press("Enter")
  await expect(page.getByRole("list", { name: "Skills" })).toBeVisible()
  await expect(page.getByText("Available", { exact: true })).toHaveCount(3)
  await page.keyboard.press("Enter")
  await page.keyboard.press("Tab")
  await expect(page.locator(".connector-account > summary")).toBeFocused()
  await page.keyboard.press("Enter")
  await expect(
    page.getByRole("button", { name: "Allow sending after review" })
  ).toBeVisible()
  await expect(page.getByText(/Email sending requires review/)).toBeVisible()
  await shot("gmail-permissions")
  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toContain("both Gmail and Calendar")
    await dialog.dismiss()
  })
  await page.getByRole("button", { name: "Disconnect Google account" }).click()
  await expect(page.locator(".connector-detail .connector-status")).toHaveText(
    "Connected"
  )
  await page.getByRole("button", { name: "Connections", exact: true }).click()
  await expect(page.getByRole("button", { name: "Manage Gmail" })).toBeVisible()
  await shot("list")
  await page.getByRole("button", { name: "Back to chat" }).click()
  await expect(page.getByLabel("Your request")).toBeVisible()
})

test("Calendar permissions, selections, working hours and responsive light/dark layouts", async () => {
  await open("Google Calendar")
  await expect(page.getByText("All selected calendars checked")).toBeVisible()
  await expect(
    page.getByText("Permission needed for some features")
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Enable event creation" })
  ).toBeVisible()
  await shot("calendar")
  await page.locator(".connector-features > summary").click()
  await expect(
    page.getByText("Permission needed", { exact: true })
  ).toHaveCount(2)
  await page.locator(".connector-account > summary").click()
  await expect(
    page.getByRole("button", { name: "Allow event details", exact: true })
  ).toBeVisible()
  await page.setViewportSize({ width: 380, height: 1200 })
  await shot("calendar-permissions")
  await page.locator(".working-hours > summary").click()
  for (const width of [320, 375, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await fits()
  }
  await page.setViewportSize({ width: 320, height: 900 })
  await page.getByLabel("Timezone", { exact: true }).scrollIntoViewIfNeeded()
  await page.screenshot({
    path: path.join("test-results", "connections-working-hours-320.png")
  })
  await page.getByLabel("Holidays in India", { exact: true }).focus()
  await page.keyboard.press("Space")
  await expect(page.getByRole("button", { name: "Save changes" })).toBeEnabled()
  await page.keyboard.press("Space")
  await page.getByRole("button", { name: "Back to chat" }).click()
  await page.evaluate(() => chrome.storage.local.set({ darkMode: true }))
  await page.reload()
  await open("Google Calendar")
  await page.setViewportSize({ width: 380, height: 900 })
  await expect(page.getByText("All selected calendars checked")).toBeVisible()
  await shot("calendar-dark")
})

test("disconnected Gmail and Calendar have obvious connect actions", async () => {
  overrides["/assistant/capabilities"] = { data: capabilities(false) }
  await page.reload()
  await open("Google Calendar")
  await expect(page.locator(".connector-detail .connector-status")).toHaveText(
    "Not connected"
  )
  await expect(
    page.getByRole("button", { name: "Connect Calendar", exact: true })
  ).toBeVisible()
  await shot("calendar-disconnected")
  await page.getByRole("button", { name: "Connections", exact: true }).click()
  await page.getByRole("button", { name: "Manage Gmail" }).click()
  await expect(
    page.getByRole("button", { name: "Connect Gmail", exact: true })
  ).toBeVisible()
  await shot("gmail-disconnected")
})

test("loading can be dismissed and calendar errors can be retried", async () => {
  let release!: () => void
  const wait = new Promise<void>((resolve) => {
    release = resolve
  })
  overrides["/calendar/calendars"] = { wait, data: { calendars: [] } }
  await open("Google Calendar")
  await expect(page.getByText("Loading your calendars…")).toBeVisible()
  await shot("loading")
  await page.getByRole("button", { name: "Back to chat" }).click()
  release()
  await expect(page.getByLabel("Your request")).toBeVisible()
  overrides["/calendar/calendars"] = {
    status: 503,
    data: {
      error: {
        code: "service_unavailable",
        message: "Calendars are temporarily unavailable. Try again."
      }
    }
  }
  await open("Google Calendar")
  await expect(page.getByRole("alert")).toContainText(
    "Calendars are temporarily unavailable"
  )
  await shot("error")
  delete overrides["/calendar/calendars"]
  await page.getByRole("button", { name: "Reload calendars" }).click()
  await expect(page.getByText("All selected calendars checked")).toBeVisible()
})

test("reconnect shows pending and cancellation feedback without opening Google", async () => {
  await open("Gmail")
  // This scenario stubs only the LOGIN response, keeping all other bridge traffic real.
  await page.evaluate(() => {
    const send = chrome.runtime.sendMessage.bind(chrome.runtime)
    ;(chrome.runtime as any).sendMessage = (message: any) => {
      if (message.type !== "LOGIN") return send(message)
      return new Promise((resolve) => {
        ;(window as any).finishLogin = resolve
      })
    }
  })
  await page.getByRole("button", { name: "Reconnect Gmail" }).click()
  await expect(
    page.getByRole("button", { name: "Reconnect Gmail" })
  ).toBeDisabled()
  await expect(page.getByText("Updating connection…")).toBeVisible()
  await shot("reconnecting")
  await page.evaluate(() =>
    (window as any).finishLogin({
      ok: false,
      error: { message: "Connection cancelled. Try again." }
    })
  )
  await expect(page.getByText("Connection cancelled. Try again.")).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Reconnect Gmail" })
  ).toBeEnabled()
  await shot("reconnect-error")
})

test("long account and calendar names wrap at 320px in both themes", async () => {
  const longEmail =
    "alexandra.long-account-name+calendar.connections@example.test"
  const longCalendar =
    "Shared calendar for the international product and customer experience team"
  await page.evaluate(async (email) => {
    const { threadlySession } =
      await chrome.storage.local.get("threadlySession")
    await chrome.storage.local.set({
      threadlySession: {
        ...threadlySession,
        user: { ...threadlySession.user, email }
      }
    })
  }, longEmail)
  overrides["/calendar/calendars"] = {
    data: {
      calendars: [
        { id: "primary", summary: longCalendar, can_read_busy: true },
        {
          id: "secondary",
          summary: "CalendarWithAVeryLongUnbrokenNameForNarrowPanelTesting",
          can_read_busy: true
        }
      ]
    }
  }
  for (const dark of [false, true]) {
    await page.evaluate(
      (darkMode) => chrome.storage.local.set({ darkMode }),
      dark
    )
    await page.reload()
    await page.setViewportSize({ width: 320, height: 1000 })
    await open("Google Calendar")
    await expect(page.getByText(longEmail, { exact: true })).toBeVisible()
    await expect(page.getByLabel(longCalendar, { exact: true })).toBeVisible()
    await fits()
    await shot(`long-names-${dark ? "dark" : "light"}-320`)
  }
})
