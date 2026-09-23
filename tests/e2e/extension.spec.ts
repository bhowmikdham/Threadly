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

let server: Server,
  context: BrowserContext,
  page: Page,
  profile: string,
  origin: string
let lastAction: any = null
test.describe.configure({ mode: "serial" })
let calls: { path: string; body: any }[] = [],
  tasks = new Map<string, any>(),
  artifacts = new Map<string, any>(),
  number = 0
const target = "abc123",
  thread = "def456"
const user = { id: 1, email: "tester@example.test", name: "Tester" }
function resultTask(body: any, kind: string) {
  const id = `task-${++number}`,
    aid = `artifact-${number}`
  const task = {
    task_id: id,
    instruction: body.instruction,
    state: "succeeded",
    version: 2,
    artifact_id: aid,
    error_code: null,
    context_snapshot_id: "ctx-1",
    workflow: null,
    compound: null
  }
  tasks.set(id, task)
  const content =
    kind === "summary"
      ? {
          overview: "Order 7842 totals $18.60. Pickup is at 6:20 PM.",
          actions: [],
          decisions: [],
          open_questions: []
        }
      : kind === "draft"
        ? {
            mode: body.draft_options?.reply_message_id ? "reply" : "new",
            subject: body.draft_options?.reply_message_id
              ? "Re: Test receipt"
              : "Project update",
            body: "Thank you for the update.",
            unresolved_fields: []
          }
        : kind === "schedule_options"
          ? {
              text: "Found 1 option.",
              slots: [
                {
                  id: "slot-1",
                  start: "2030-09-24T03:00:00Z",
                  end: "2030-09-24T03:30:00Z",
                  timezone: "Australia/Melbourne"
                }
              ],
              expires_at: "2030-09-24T00:00:00Z",
              slot_request_id: "slot-request-1"
            }
          : { text: "$18.60", found: true }
  artifacts.set(aid, {
    artifact_id: aid,
    task_id: id,
    revision: 1,
    is_latest: true,
    artifact: {
      kind,
      content,
      evidence: [
        {
          ref_id: "source-1",
          source_kind: "message",
          source_id: target,
          quote: "Order 7842 totals $18.60."
        }
      ],
      assumptions: [],
      coverage: "partial"
    },
    draft_envelope:
      kind === "draft"
        ? {
            to: body.draft_options?.to || ["alex@example.test"],
            cc: [],
            bcc: [],
            reply: body.draft_options?.reply_message_id
              ? {
                  subject: "Re: Test receipt",
                  gmail_message_id: target,
                  gmail_thread_id: thread
                }
              : null
          }
        : null,
    review:
      kind === "draft"
        ? { payload_hash: "a".repeat(64), state: "unreviewed", blockers: [] }
        : null
  })
  return { ...task, state: "queued", artifact_id: null }
}
test.beforeAll(async () => {
  server = createServer(async (req, res) => {
    let raw = ""
    for await (const chunk of req) raw += chunk
    const body = raw ? JSON.parse(raw) : undefined
    const p = req.url!.split("?")[0]
    calls.push({ path: req.url!, body })
    if (p === "/auth/google/begin") {
      req.socket.destroy()
      return
    }
    res.setHeader("Content-Type", "application/json")
    res.setHeader("Access-Control-Allow-Origin", "*")
    let data: any
    if (p === "/assistant/capabilities")
      data = {
        capabilities: [
          { id: "gmail_read", ready: true },
          { id: "calendar_read", ready: true },
          { id: "gmail_send", ready: false, status: "disabled" }
        ]
      }
    else if (p === "/assistant/mail-search")
      data = {
        results: [
          {
            message_id: target,
            thread_id: thread,
            subject: "Test receipt",
            received_at: "2026-09-23T03:00:00Z"
          }
        ],
        next_cursor: null
      }
    else if (p === `/threads/${thread}`)
      data = {
        thread: { thread_id: thread, version: 1, subject: "Test receipt" },
        messages: [
          {
            gmail_msg_id: target,
            from_addr: "supplier@example.test",
            subject: "Test receipt",
            body_clean: "Order 7842 totals $18.60.",
            sent_at: "2026-09-23T03:00:00Z"
          }
        ]
      }
    else if (p === "/assistant/context-snapshots") {
      expect(body.ui_map.selected_message_ids).toEqual([target])
      expect(body).not.toHaveProperty("messages")
      data = {
        context_snapshot_id: "ctx-1",
        thread_id: thread,
        thread_version: 1
      }
    } else if (p === "/assistant/requests")
      data = resultTask(
        body,
        body.intent_hint === "summarise"
          ? "summary"
          : ["reply", "compose"].includes(body.intent_hint)
            ? "draft"
            : "answer"
      )
    else if (p === "/assistant/tasks")
      data = { tasks: [...tasks.values()], next_cursor: null }
    else if (/^\/assistant\/tasks\/[^/]+$/.test(p))
      data = tasks.get(p.split("/").at(-1)!)
    else if (/^\/assistant\/artifacts\/[^/]+$/.test(p))
      data = artifacts.get(p.split("/").at(-1)!)
    else if (p.endsWith("/draft-revisions") && req.method === "POST") {
      const t = tasks.get(p.split("/")[3])
      const a = artifacts.get(t.artifact_id)
      data = {
        ...a,
        revision: a.revision + 1,
        artifact_id: a.artifact_id + "-edit",
        artifact: {
          ...a.artifact,
          content: {
            ...a.artifact.content,
            body: body.body,
            subject: body.subject
          }
        },
        draft_envelope: { ...a.draft_envelope, ...body.recipients }
      }
      artifacts.set(data.artifact_id, data)
      t.artifact_id = data.artifact_id
    } else if (p.endsWith("/draft-revisions"))
      data = { revisions: [], next_before_revision: null }
    else if (p.endsWith("/review")) data = artifacts.get(p.split("/")[3])
    else if (p.endsWith("/actions")) {
      const a = artifacts.get(p.split("/")[3])
      data = {
        action_id: "action-1",
        state: "proposed",
        version: 1,
        payload_hash: "b".repeat(64),
        preview: {
          from_address: user.email,
          ...a.draft_envelope,
          subject: a.artifact.content.subject,
          body: a.artifact.content.body
        },
        blockers: ["email_writes_disabled"],
        approval_available: false,
        sending_available: false,
        allowed_operations: ["reject"]
      }
      lastAction = data
    } else if (p === "/assistant/actions/action-1") data = lastAction
    else if (p === "/calendar/preferences")
      data = {
        version: 1,
        preferences: {
          timezone: "Australia/Melbourne",
          calendar_ids: ["primary"],
          working_periods: [
            { weekday: 0, start_minute: 540, end_minute: 1020 }
          ],
          default_duration_minutes: 30,
          minimum_notice_minutes: 60,
          buffer_before_minutes: 0,
          buffer_after_minutes: 0
        }
      }
    else if (p === "/calendar/calendars")
      data = {
        calendars: [
          {
            id: "primary",
            summary: "Personal calendar",
            can_read_busy: true,
            event_write_acl: true
          }
        ]
      }
    else if (p === "/assistant/workflow-proposals")
      data = {
        plan_id: "plan-1",
        state: "proposed",
        plan_hash: "c".repeat(64),
        expires_at: "2030-01-01T00:00:00Z",
        request: body,
        result: {
          clauses: [
            {
              kind: "requested",
              text: body.instruction,
              operations: ["schedule"]
            }
          ],
          questions: [],
          reason: null
        }
      }
    else if (p === "/assistant/workflow-proposals/plan-1/confirm") {
      expect(body.plan_hash).toBe("c".repeat(64))
      expect(body.confirm_complete_command).toBe(true)
      data = resultTask(
        { instruction: "Find a meeting time" },
        "schedule_options"
      )
    } else {
      res.statusCode = 404
      data = {
        error: { code: "not_found", message: "Unexpected test route " + p }
      }
    }
    res.end(JSON.stringify(data))
  })
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r))
  origin = `http://127.0.0.1:${(server.address() as any).port}`
  profile = await mkdtemp(path.join(tmpdir(), "threadly-extension-test-"))
  const extension = path.resolve("build/chrome-mv3-prod")
  context = await chromium.launchPersistentContext(profile, {
    channel: "chromium",
    headless: true,
    args: [
      `--disable-extensions-except=${extension}`,
      `--load-extension=${extension}`
    ]
  })
  let worker =
    context.serviceWorkers()[0] || (await context.waitForEvent("serviceworker"))
  const jwt =
    "header." +
    Buffer.from(
      JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })
    ).toString("base64url") +
    ".signature"
  await worker.evaluate(
    async ({ origin, jwt, user }) => {
      await chrome.storage.local.set({ backendOrigin: origin })
      await chrome.storage.session.set({
        threadlySession: { jwt, user, origin }
      })
    },
    { origin, jwt, user }
  )
  page = await context.newPage()
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto(
    `chrome-extension://${worker.url().split("/")[2]}/sidepanel.html`
  )
})
test.afterAll(async () => {
  await context?.close()
  await new Promise<void>((r) => server?.close(() => r()))
  if (profile) await rm(profile, { recursive: true, force: true })
})
test("real extension bridge: selected summary, answer and edited reply without send", async () => {
  await page.getByRole("button", { name: "Search mail", exact: true }).click()
  await page.getByLabel("Search text").fill("receipt")
  await page.getByRole("button", { name: "Search", exact: true }).click()
  await page.getByRole("button", { name: "Test receipt", exact: true }).click()
  await page.getByRole("button", { name: "Use as target" }).click()
  await page.getByLabel("Request type").selectOption("summarise")
  await page.getByRole("button", { name: "Submit", exact: true }).click()
  await expect(
    page.getByText("Order 7842 totals $18.60. Pickup is at 6:20 PM.")
  ).toBeVisible()
  await page.getByLabel("Request type").selectOption("ask")
  await page.getByLabel("Your request").fill("How much did I pay?")
  await page.getByRole("button", { name: "Submit", exact: true }).click()
  await expect(page.getByText("$18.60", { exact: true })).toBeVisible()
  await page.getByLabel("Request type").selectOption("reply")
  await page.getByRole("button", { name: "Submit", exact: true }).click()
  await expect(page.getByLabel("Message", { exact: true })).toHaveValue(
    "Thank you for the update."
  )
  await page
    .getByLabel("Message", { exact: true })
    .fill("Thank you for confirming.")
  await page.getByRole("button", { name: "Save edits" }).click()
  await expect(page.getByText("Revision 2", { exact: true })).toBeVisible()
  await page.getByRole("button", { name: "Review outgoing email" }).click()
  await expect(page.getByText("Exact outgoing email")).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Approve and send" })
  ).toHaveCount(0)
  expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(false)
  expect(calls.some((c) => c.path.startsWith("/sync"))).toBe(false)
  await page.reload()
  await page.getByRole("button", { name: "History", exact: true }).click()
  await page
    .getByRole("button", { name: /Draft a reply to this thread/ })
    .click()
  await expect(page.getByText("Exact outgoing email")).toBeVisible()
  expect(calls.filter((c) => c.path.endsWith("/actions"))).toHaveLength(1)
  expect(calls.some((c) => c.path === "/assistant/actions/action-1")).toBe(true)
  await page.screenshot({
    path: path.join("test-results", "extension-preview.png"),
    fullPage: true
  })
})
test("multi-step proposal requires explicit confirmation and scheduling renders real slots", async () => {
  await page.getByLabel("Request type").selectOption("workflow")
  await page.getByLabel("Recipient (if the plan includes a draft)").fill("")
  await page.getByLabel("Your request").fill("Find a meeting time tomorrow.")
  await page.getByRole("button", { name: "Submit", exact: true }).click()
  await expect(page.getByText("Review the complete workflow")).toBeVisible()
  expect(calls.filter((c) => c.path.endsWith("/confirm"))).toHaveLength(0)
  await page
    .getByRole("button", { name: "Confirm this complete workflow" })
    .click()
  await expect(page.getByText("Found 1 option.")).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Select and recheck" })
  ).toBeVisible()
})
test("settings use real capability and versioned preference contracts; history survives panel reload", async () => {
  await page.reload()
  await page.getByRole("button", { name: "History", exact: true }).click()
  await expect(
    page
      .getByRole("heading", { name: "Recent tasks" })
      .or(page.getByText("Recent tasks", { exact: true }))
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: /succeeded/ }).first()
  ).toBeVisible()
  await page.getByRole("button", { name: "Settings", exact: true }).click()
  await page
    .getByRole("button", { name: "Load calendars and preferences" })
    .click()
  await expect(page.getByLabel("Timezone", { exact: true })).toHaveValue(
    "Australia/Melbourne"
  )
  await page
    .getByRole("button", { name: "Save scheduling preferences" })
    .click()
  await expect(page.getByText("Scheduling preferences saved.")).toBeVisible()
  expect(
    calls.find((c) => c.path === "/calendar/preferences" && c.body)?.body
      .expected_version
  ).toBe(1)
  await page.getByRole("button", { name: "Sign out", exact: true }).click()
  await expect(
    page.getByRole("button", { name: "Sign in with Google" })
  ).toBeVisible()
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
