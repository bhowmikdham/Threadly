import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import {
  chromium,
  expect,
  test,
  type BrowserContext,
  type Page
} from "@playwright/test"

import { createMockBackend, user, type MockBackend } from "./mock-backend"

let backend: MockBackend, context: BrowserContext, page: Page
let profile: string, origin: string, panelUrl: string
const extension = path.resolve("build/chrome-mv3-prod")
const token = (seconds = 3600) =>
  "header." +
  Buffer.from(
    JSON.stringify({ exp: Math.floor(Date.now() / 1000) + seconds })
  ).toString("base64url") +
  ".access"
async function launch() {
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
      .find((w) => w.url().startsWith("chrome-extension://")) ||
    (await context.waitForEvent("serviceworker"))
  panelUrl = `chrome-extension://${new URL(worker.url()).host}/sidepanel.html`
  page = await context.newPage()
  await page.goto(panelUrl)
  // STATUS is also the startup/storage-protection barrier.
  await page.evaluate(() =>
    chrome.runtime.sendMessage({ channel: "threadly", type: "STATUS" })
  )
}
async function seed(jwt = token()) {
  await page.evaluate(
    async ({ origin, jwt, user }) => {
      await chrome.storage.local.set({
        backendOrigin: origin,
        threadlySession: {
          origin,
          jwt,
          user,
          refresh_token: "synthetic-renewal-token"
        }
      })
    },
    { origin, jwt, user }
  )
}
async function api() {
  return page.evaluate(() =>
    chrome.runtime.sendMessage({
      channel: "threadly",
      type: "API",
      path: "/assistant/capabilities",
      method: "GET"
    })
  )
}
async function stored() {
  return page.evaluate(
    async () =>
      (await chrome.storage.local.get("threadlySession")).threadlySession
  )
}
async function logout() {
  return page.evaluate(() =>
    chrome.runtime.sendMessage({ channel: "threadly", type: "LOGOUT" })
  )
}
test.beforeEach(async () => {
  backend = createMockBackend()
  backend.control.calendarConnected = false
  await new Promise<void>((r) => backend.server.listen(0, "127.0.0.1", r))
  origin = `http://127.0.0.1:${(backend.server.address() as any).port}`
  profile = await mkdtemp(path.join(tmpdir(), "threadly-login-test-"))
  await launch()
})
test.afterEach(async () => {
  await context.close()
  await new Promise<void>((r) => backend.server.close(() => r()))
  await rm(profile, { recursive: true, force: true })
})

test("extension reload retains login and can make an authenticated request", async () => {
  await seed()
  const management = await context.newPage()
  await management.goto("chrome://extensions/")
  await management.locator("#devMode").click()
  await management
    .locator("extensions-item")
    .locator("#dev-reload-button")
    .click()
  page = await context.newPage()
  await expect
    .poll(
      async () => {
        try {
          if (page.isClosed()) page = await context.newPage()
          await page.goto(panelUrl)
          return await api()
        } catch (error) {
          return { error: String(error) }
        }
      },
      { timeout: 15000 }
    )
    .toMatchObject({ ok: true })
  expect((await stored()).user.id).toBe(user.id)
  expect(
    await page.evaluate(() => chrome.storage.session.get("threadlySession"))
  ).toEqual({})
  await expect(page.getByLabel("Your request")).toBeVisible()
})

test("browser restart retains login, then sign-out remains signed out after another restart", async () => {
  await seed()
  await context.close()
  await launch()
  expect(await api()).toMatchObject({ ok: true })
  await expect(page.getByLabel("Your request")).toBeVisible()
  expect(await logout()).toMatchObject({
    ok: true,
    data: { serverRevoked: true }
  })
  await context.close()
  await launch()
  expect(await stored()).toBeUndefined()
  await expect(
    page.getByRole("button", { name: "Sign in with Google" })
  ).toBeVisible()
})

test("expired access token renews with the renewal credential after browser restart", async () => {
  let correctRenewalCredential = false
  backend.server.on("request", (req) => {
    if (req.url === "/auth/refresh")
      correctRenewalCredential =
        req.headers.authorization === "Bearer synthetic-renewal-token"
  })
  await seed(token(-3600))
  await context.close()
  await launch()
  await expect
    .poll(async () => (await stored())?.jwt.endsWith(".refreshed"))
    .toBe(true)
  expect(correctRenewalCredential).toBe(true)
  expect(await api()).toMatchObject({ ok: true })
  expect(
    backend.calls.filter((c) => c.path.startsWith("/auth/google/"))
  ).toEqual([])
})

test("temporary renewal failure retains login for retry; confirmed revocation clears it", async () => {
  await seed(token(-3600))
  backend.control.failRefresh = 503
  expect(await api()).toMatchObject({ ok: false, error: { status: 503 } })
  expect(await stored()).toBeDefined()
  backend.control.failRefresh = undefined
  expect(await api()).toMatchObject({ ok: true })
  await seed(token(-3600))
  backend.control.failRefresh = 401
  expect(await api()).toMatchObject({ ok: false, error: { status: 401 } })
  expect(await stored()).toBeUndefined()
  await context.close()
  await launch()
  expect(await stored()).toBeUndefined()
})

test("changing the development server clears persistent login without sending its token", async () => {
  await seed()
  const result = await page.evaluate(() =>
    chrome.runtime.sendMessage({
      channel: "threadly",
      type: "CONFIGURE",
      origin: "http://127.0.0.1:19999"
    })
  )
  expect(result.ok).toBe(true)
  expect(await stored()).toBeUndefined()
  expect(backend.calls.filter((c) => c.authorized)).toEqual([])
})

for (const path of ["/assistant/capabilities", "/auth/refresh"]) {
  test(`a delayed ${path} rejection cannot discard a newer login`, async () => {
    await seed(path === "/auth/refresh" ? token(-3600) : token())
    const worker = context.serviceWorkers()[0]
    await worker.evaluate((path) => {
      const original = globalThis.fetch
      globalThis.fetch = async (input, init) => {
        if (String(input).endsWith(path)) {
          ;(globalThis as any).requestHeld = true
          await new Promise<void>((resolve) => {
            ;(globalThis as any).releaseRequest = resolve
          })
          return new Response(
            JSON.stringify({
              error: { code: "reauth_required", message: "Expired session." }
            }),
            { status: 401 }
          )
        }
        return original(input, init)
      }
    }, path)
    const pending = api()
    await expect
      .poll(() => worker.evaluate(() => !!(globalThis as any).requestHeld))
      .toBe(true)
    const newer = token(7200)
    await seed(newer)
    await worker.evaluate(() => (globalThis as any).releaseRequest())
    expect(await pending).toMatchObject({
      ok: false,
      error: { status: 409, code: "session_changed" }
    })
    expect((await stored()).jwt).toBe(newer)
  })
}
