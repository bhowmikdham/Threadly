import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import { chromium, expect, test } from "@playwright/test"

import { createMockBackend, user } from "./mock-backend"

for (const needsConsent of [false, true]) {
  test(`named draft preserves edits, keyboard access and 320px layout (consent: ${needsConsent})`, async () => {
    const { server, calls, control } = createMockBackend()
    control.gmailDraftReady = !needsConsent
    await new Promise<void>((r) => server.listen(0, "127.0.0.1", r))
    const origin = `http://127.0.0.1:${(server.address() as any).port}`
    const profile = await mkdtemp(
      path.join(tmpdir(), "threadly-draft-preview-")
    )
    const extension = path.resolve("build/chrome-mv3-prod")
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
        context.serviceWorkers()[0] ||
        (await context.waitForEvent("serviceworker"))
      const page = await context.newPage()
      await page.setViewportSize({ width: 420, height: 900 })
      await page.goto(
        `chrome-extension://${new URL(worker.url()).host}/sidepanel.html`
      )
      const jwt =
        "header." +
        Buffer.from(
          JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })
        ).toString("base64url") +
        ".signature"
      await page.evaluate(
        async (value) => {
          await chrome.storage.local.set({
            backendOrigin: value.origin,
            threadlySession: value
          })
        },
        { origin, user, jwt }
      )
      await page.reload()
      await page
        .getByLabel("Your request")
        .fill("Draft an email to Alex thanking them for their help")
      await page.getByLabel("Your request").press("Enter")
      const card = page.getByLabel("Email draft", { exact: true })
      await expect(card).toBeVisible()
      if (needsConsent) {
        await worker.evaluate(() => {
          let attempts = 0
          ;(chrome.identity as any).launchWebAuthFlow = async ({
            url
          }: {
            url: string
          }) => {
            if (++attempts === 1) throw new Error("Sign-in was cancelled.")
            return (
              chrome.identity.getRedirectURL("oauth/callback") +
              "?code=synthetic-code&state=" +
              new URL(url).searchParams.get("state")
            )
          }
        })
        const wrongAccount = await page.evaluate(() =>
          chrome.runtime.sendMessage({
            channel: "threadly",
            type: "LOGIN",
            capabilities: ["gmail_draft"],
            expectedUserId: 999
          })
        )
        expect(wrongAccount.ok).toBe(false)
        expect(calls.some((c) => c.path === "/auth/google/reconnect")).toBe(
          false
        )
        await card
          .getByLabel("Subject", { exact: true })
          .fill("Edited before consent")
        await card
          .getByLabel("Message", { exact: true })
          .fill("Keep my exact edits  ")
        await page.setViewportSize({ width: 320, height: 1100 })
        await expect(
          card.getByText(
            /Google’s permission includes managing drafts and sending email/
          )
        ).toBeVisible()
        await page.screenshot({
          path: "test-results/gmail-draft-consent-320.png"
        })
        await card
          .getByRole("button", { name: "Enable draft creation" })
          .click()
        await expect(card.getByRole("alert")).toContainText("cancelled")
        await expect(card.getByLabel("Message", { exact: true })).toHaveValue(
          "Keep my exact edits  "
        )
        await card
          .getByRole("button", { name: "Enable draft creation" })
          .click()
        await expect(card.getByText(/Draft access is ready/)).toBeVisible()
        await expect(card.getByLabel("Subject", { exact: true })).toHaveValue(
          "Edited before consent"
        )
        await expect(card.getByLabel("Message", { exact: true })).toHaveValue(
          "Keep my exact edits  "
        )
        expect(
          calls.filter(
            (c) => c.path === "/assistant/gmail-drafts" && c.method === "POST"
          )
        ).toHaveLength(0)
        expect(
          calls
            .filter((c) => c.path === "/auth/google/reconnect")
            .every(
              (c) =>
                c.body.capabilities.length === 1 &&
                c.body.capabilities[0] === "gmail_draft"
            )
        ).toBe(true)
        await page.setViewportSize({ width: 420, height: 900 })
      }
      await expect(card.getByLabel("To", { exact: true })).toHaveValue("Alex")
      await expect(
        card.getByRole("button", { name: "Create draft", exact: true })
      ).toBeEnabled()
      await card.getByLabel("Subject", { exact: true }).fill("Thank you, Alex")
      await card
        .getByLabel("Message", { exact: true })
        .fill(
          "Hi Alex,\n\nThank you for helping me with the presentation. Your feedback made a real difference.\n\nBest,\nSam"
        )
      await card.scrollIntoViewIfNeeded()
      await page.screenshot({ path: "test-results/gmail-draft-light-420.png" })
      await card
        .getByRole("button", { name: "Create draft", exact: true })
        .click()
      await expect(card.getByRole("alert")).toContainText("email addresses")
      expect(
        calls.filter(
          (c) => c.method === "POST" && c.path === "/assistant/gmail-drafts"
        )
      ).toHaveLength(0)
      await card.getByLabel("To", { exact: true }).fill("alex@example.test")
      await card.getByRole("button", { name: "Cc / Bcc", exact: true }).click()
      await card.getByLabel("Bcc", { exact: true }).fill("copy@example.test")
      for (const dark of [false, true]) {
        await page.setViewportSize({ width: 320, height: 1100 })
        await page.evaluate(
          (dark) =>
            document.querySelector(".threadly")?.classList.toggle("dark", dark),
          dark
        )
        await card.scrollIntoViewIfNeeded()
        await expect
          .poll(() =>
            page.evaluate(
              () => document.documentElement.scrollWidth <= innerWidth
            )
          )
          .toBe(true)
        await page.screenshot({
          animations: "disabled",
          path: `test-results/gmail-draft-${dark ? "dark" : "light"}-320.png`
        })
      }
      await card
        .getByLabel("Subject", { exact: true })
        .fill("A very long subject ".repeat(35))
      await card
        .getByLabel("Message", { exact: true })
        .fill("Long paragraph with details. ".repeat(500))
      await expect
        .poll(() =>
          page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth
          )
        )
        .toBe(true)
      await card.getByLabel("Subject", { exact: true }).fill("Thank you, Alex")
      await card
        .getByLabel("Message", { exact: true })
        .fill("Exact edited body\n\nWith a final paragraph.")
      await card
        .getByRole("button", { name: "Create draft", exact: true })
        .focus()
      await page.keyboard.press("Enter")
      await expect(card.getByText("Saved to Gmail Drafts")).toBeVisible()
      const writes = calls.filter(
        (c) => c.method === "POST" && c.path === "/assistant/gmail-drafts"
      )
      expect(writes).toHaveLength(1)
      expect(writes[0].body).toMatchObject({
        subject: "Thank you, Alex",
        body: "Exact edited body\n\nWith a final paragraph.",
        recipients: {
          to: ["alex@example.test"],
          cc: [],
          bcc: ["copy@example.test"]
        }
      })
      expect(calls.some((c) => /\/approve$|\/send$/.test(c.path))).toBe(false)
      await card.scrollIntoViewIfNeeded()
      await page.screenshot({
        animations: "disabled",
        path: "test-results/gmail-draft-created-320.png"
      })
    } finally {
      await context.close()
      await new Promise<void>((r) => server.close(() => r()))
      await rm(profile, { recursive: true, force: true })
    }
  })
}
