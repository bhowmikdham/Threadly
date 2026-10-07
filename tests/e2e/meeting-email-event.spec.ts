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

import { createMockBackend, user, type MockBackend } from "./mock-backend"

let backend: MockBackend, server: Server, context: BrowserContext, page: Page
let profile: string, calls: { path: string; body: any }[], actions: any[]
const source = {
  kind: "gmail_message",
  thread_id: "abc123",
  message_id: "def456"
}
test.beforeEach(async () => {
  backend = createMockBackend()
  calls = []
  actions = []
  server = createServer(async (req, res) => {
    const url = req.url!
    if (
      !url.startsWith("/calendar/meeting-email/") &&
      !url.startsWith("/assistant/calendar-actions/meeting-") &&
      url !== "/assistant/conversation-turns"
    ) {
      backend.server.emit("request", req, res)
      return
    }
    let raw = ""
    for await (const chunk of req) raw += chunk
    const body = raw ? JSON.parse(raw) : null
    calls.push({ path: url, body })
    res.setHeader("Content-Type", "application/json")
    res.setHeader("Access-Control-Allow-Origin", "*")
    let data: any
    if (url === "/assistant/conversation-turns")
      data = {
        conversation_id: body.conversation_id,
        version: body.expected_version + 1,
        context_memory_version: 1,
        kind: "message",
        text: "Here is the meeting email.",
        search: {
          filters: { folder: "INBOX", timezone: "UTC" },
          results: [
            {
              reference: "mail-1",
              thread_id: source.thread_id,
              message_id: source.message_id,
              subject: "Meeting email result",
              sender: "sender@example.test",
              received_at: "2026-10-07T10:00:00Z",
              snippet: "Meet to discuss the agenda."
            }
          ],
          coverage: { complete: true, page_size: 1 },
          next_cursor: null
        }
      }
    else if (url.endsWith("/draft"))
      data = {
        source,
        context_snapshot_id: "fresh-owned-capture",
        title: "Fresh agenda meeting",
        source_subject: "Fresh agenda meeting",
        source_sender: "sender@example.test",
        source_excerpt: "Please review the agenda before our meeting.",
        source_truncated: false,
        timezone: "UTC",
        default_duration_minutes: 30,
        preferences_version: 1,
        calendars: [{ id: "work", name: "Work" }],
        confirmation_required: true
      }
    else if (url.endsWith("/previews")) {
      data = {
        action_id: "meeting-" + (actions.length + 1),
        state: "proposed",
        version: 1,
        payload_hash: "exact-" + (actions.length + 1),
        approval_available: true,
        blockers: [],
        authorization: "separate_exact_event_approval",
        preview: {
          calendar_id: body.calendar_id,
          calendar_name: "Work",
          send_updates: body.send_updates,
          event: {
            summary: body.title,
            description: body.description,
            location: body.location,
            attendees: body.attendees.map((email: string) => ({ email })),
            start: {
              dateTime: body.date + "T" + body.start_time + ":00Z",
              timeZone: "UTC"
            },
            end: { dateTime: body.date + "T15:00:00Z", timeZone: "UTC" }
          }
        }
      }
      actions.push(data)
    } else {
      data = actions.find((a) => url.includes(a.action_id))
      if (url.endsWith("/approve") || url.endsWith("/reject")) {
        expect(body.expected_version).toBe(data.version)
        if (url.endsWith("/approve"))
          expect(body.payload_hash).toBe(data.payload_hash)
        data.state = url.endsWith("/approve") ? "succeeded" : "rejected"
        data.approval_available = false
        data.version++
      }
    }
    res.end(JSON.stringify(data))
  })
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve))
  const origin = `http://127.0.0.1:${(server.address() as any).port}`
  profile = await mkdtemp(path.join(tmpdir(), "threadly-meeting-email-"))
  const extension = path.resolve("build/chrome-mv3-prod")
  context = await chromium.launchPersistentContext(profile, {
    channel: "chromium",
    headless: true,
    viewport: { width: 360, height: 900 },
    args: [
      `--disable-extensions-except=${extension}`,
      `--load-extension=${extension}`
    ]
  })
  const worker =
    context.serviceWorkers()[0] || (await context.waitForEvent("serviceworker"))
  await expect
    .poll(() =>
      worker.evaluate(() => Boolean(globalThis.chrome?.storage?.local))
    )
    .toBe(true)
  await worker.evaluate(
    async ({ origin, user }) => {
      await chrome.storage.local.set({
        backendOrigin: origin,
        threadlySession: {
          origin,
          user,
          refresh_token: "synthetic-refresh",
          jwt:
            "fixture." +
            btoa(
              JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })
            ) +
            ".access"
        }
      })
    },
    { origin, user }
  )
  page = await context.newPage()
  await page.goto(
    `chrome-extension://${worker.url().split("/")[2]}/sidepanel.html`
  )
  await page.getByLabel("Your request").fill("Find the meeting email")
  await page.getByRole("button", { name: "Send request" }).click()
  await expect(
    page.getByRole("button", { name: "Use email: Meeting email result" })
  ).toBeVisible()
})
test.afterEach(async () => {
  await context?.close()
  await new Promise<void>((resolve) => server?.close(() => resolve()))
  if (profile) await rm(profile, { recursive: true, force: true })
})
test("email button → editable preview → edit → explicit confirmation under Always", async () => {
  await page
    .getByRole("button", { name: "Calendar approval: Ask for approval" })
    .click()
  await page.getByRole("menuitemradio", { name: /Always allow/ }).click()
  await expect(
    page.getByRole("button", { name: "Calendar approval: Always allow" })
  ).toBeVisible()
  await page.getByRole("button", { name: "Create event", exact: true }).click()
  await expect(page.getByLabel("Title", { exact: true })).toHaveValue(
    "Fresh agenda meeting"
  )
  await expect(page.getByLabel("Date (required)")).toHaveValue("")
  await expect(page.getByLabel("Attendees (optional)")).toHaveValue("")
  expect(calls.find((c) => c.path.endsWith("/draft"))?.body).toEqual({ source })
  expect(actions).toHaveLength(0)
  await page.getByLabel("Date (required)").fill("2030-10-09")
  await page.getByLabel("Start time (required)").fill("14:00")
  await page.getByLabel("Attendees (optional)").fill("guest@example.test")
  await expect(
    page.getByRole("button", { name: "Review event preview" })
  ).toBeDisabled()
  await page.getByRole("checkbox", { name: /Send invitations/ }).check()
  await page.getByRole("button", { name: "Review event preview" }).click()
  await expect(page.getByText("Ready to review")).toBeVisible()
  expect(actions[0].state).toBe("proposed")
  expect(calls.filter((c) => c.path.endsWith("/approve"))).toHaveLength(0)
  await page.getByRole("button", { name: "Edit details" }).click()
  await page.getByLabel("Title", { exact: true }).fill("Edited agenda meeting")
  expect(actions[0].state).toBe("rejected")
  await page.getByRole("button", { name: "Review event preview" }).click()
  await expect(
    page.getByRole("heading", { name: "Edited agenda meeting" })
  ).toBeVisible()
  expect(calls.filter((c) => c.path.endsWith("/approve"))).toHaveLength(0)
  await page
    .getByRole("button", { name: "Create event & send invitations" })
    .click()
  await expect(page.getByText("Event created", { exact: true })).toBeVisible()
  expect(calls.filter((c) => c.path.endsWith("/approve"))).toHaveLength(1)
  expect(actions[1].state).toBe("succeeded")
  expect(calls.find((c) => c.path.endsWith("/previews"))?.body).toMatchObject({
    source,
    context_snapshot_id: "fresh-owned-capture",
    attendees: ["guest@example.test"],
    send_updates: "all"
  })
  expect(
    backend.calls.filter((c) =>
      /\/auth\/(google|logout|disconnect)/.test(c.path)
    )
  ).toEqual([])
  await page.getByLabel("Your request").fill("Return to my earlier work")
  await page.getByRole("button", { name: "Send request" }).click()
  await expect
    .poll(() => calls.filter((c) => c.path === "/assistant/conversation-turns").length)
    .toBe(2)
  const turns = calls.filter((c) => c.path === "/assistant/conversation-turns")
  expect(turns[1].body.conversation_id).toBe(turns[0].body.conversation_id)
  expect(turns[1].body.expected_version).toBe(1)
  expect(turns[1].body).not.toHaveProperty("active_task_id")
  expect(turns[1].body).not.toHaveProperty("context_snapshot_id")
  expect(calls.filter((c) => c.path.endsWith("/approve"))).toHaveLength(1)
})
