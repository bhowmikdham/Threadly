import { mkdtemp, readFile, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import { chromium, expect, test } from "@playwright/test"

const publicOrigin = process.env.PLASMO_PUBLIC_THREADLY_BACKEND_ORIGIN

test("packaged HTTPS build pins its server and hides developer setup", async () => {
  test.skip(!publicOrigin, "Build with PLASMO_PUBLIC_THREADLY_BACKEND_ORIGIN")
  const profile = await mkdtemp(path.join(tmpdir(), "threadly-public-build-"))
  const extension = path.resolve(
    process.env.THREADLY_E2E_EXTENSION_PATH || "build/chrome-mv3-prod"
  )
  const manifest = JSON.parse(
    await readFile(path.join(extension, "manifest.json"), "utf8")
  )
  expect(manifest.optional_host_permissions).toContain("https://*/*")
  const context = await chromium.launchPersistentContext(profile, {
    channel: "chromium",
    headless: true,
    args: [
      `--disable-extensions-except=${extension}`,
      `--load-extension=${extension}`
    ]
  })
  try {
    const worker =
      context
        .serviceWorkers()
        .find((item) => item.url().startsWith("chrome-extension://")) ||
      (await context.waitForEvent("serviceworker", {
        predicate: (item) => item.url().startsWith("chrome-extension://")
      }))
    const page = await context.newPage()
    await page.goto(
      `chrome-extension://${new URL(worker.url()).host}/sidepanel.html`
    )
    await expect(
      page.getByRole("button", { name: "Sign in with Google" })
    ).toBeVisible()
    const status = await page.evaluate(() =>
      chrome.runtime.sendMessage({ channel: "threadly", type: "STATUS" })
    )
    expect(status.data.origin).toBe(publicOrigin)
    await page.evaluate(() => {
      ;(globalThis as any).requestedOrigins = []
      ;(chrome.permissions as any).request = async (details: {
        origins: string[]
      }) => {
        ;(globalThis as any).requestedOrigins.push(details.origins)
        return false
      }
    })
    await page.getByRole("button", { name: "Sign in with Google" }).click()
    await expect(page.getByRole("alert")).toContainText(
      "Allow Threadly to connect to its server"
    )
    expect(
      await page.evaluate(() => (globalThis as any).requestedOrigins)
    ).toEqual([[`${publicOrigin}/*`]])
    const rejected = await page.evaluate(() =>
      chrome.runtime.sendMessage({
        channel: "threadly",
        type: "CONFIGURE",
        origin: "http://127.0.0.1:8000"
      })
    )
    expect(rejected.ok).toBe(false)
    await page.getByRole("button", { name: "Settings", exact: true }).click()
    await expect(page.getByText("Backend server")).toHaveCount(0)
    await expect(page.getByText("Google sign-in setup")).toHaveCount(0)
    await expect(page.getByText("EC2 development")).toHaveCount(0)
    // The public package's settings bridge works with no authenticated account.
    // Gmail is a local synthetic document; classification never contacts a provider.
    await expect(
      page.getByRole("heading", { name: "A more readable inbox" })
    ).toBeVisible()
    const words = page.getByRole("checkbox", {
      name: "Rather have words than icons"
    })
    await words.check()
    await expect(words).toBeChecked()
    await context.route("https://mail.google.com/**", (route) =>
      route.fulfill({
        contentType: "text/html",
        body: '<!doctype html><meta charset="utf-8"><title>Synthetic Gmail</title><div role="main">Synthetic inbox</div>'
      })
    )
    const gmail = await context.newPage()
    await gmail.goto("https://mail.google.com/mail/u/0/#inbox")
    await expect(gmail.locator("html")).toHaveAttribute(
      "data-threadly-mark-words",
      "true"
    )
    await page.getByRole("checkbox", { name: "Show marks in Gmail" }).uncheck()
    await expect(gmail.locator("html")).toHaveAttribute(
      "data-threadly-badges",
      "off"
    )
    await words.uncheck()
    await expect(gmail.locator("html")).toHaveAttribute(
      "data-threadly-mark-words",
      "false"
    )
    await page.reload()
    await page.getByRole("button", { name: "Settings", exact: true }).click()
    await expect(
      page.getByRole("checkbox", { name: "Show marks in Gmail" })
    ).not.toBeChecked()
  } finally {
    await context.close()
    await rm(profile, { recursive: true, force: true })
  }
})
