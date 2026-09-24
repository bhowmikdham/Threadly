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
    context_snapshot_id: body.context_snapshot_id || null,
    effective_context_snapshot_id: body.context_snapshot_id || null,
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
    let p = req.url!.split("?")[0]
    const conversational = p === "/assistant/conversation-turns"
    const originalTurn = conversational ? { ...body } : null
    calls.push({ path: req.url!, body: body ? structuredClone(body) : body })
    if (conversational) {
      // A deterministic API fixture. Actual semantic decisions are evaluated against Bedrock.
      if (
        body.instruction === "hey" ||
        (/^show.*emails/i.test(body.instruction) &&
          !/next page/.test(body.instruction))
      )
        p = "/assistant/inbox-chat"
      else if (/next page/.test(body.instruction))
        p = "/assistant/inbox-search-page"
      else if (body.instruction === "alex@example.test") {
        const t = tasks.get(body.active_task_id)
        p = `/assistant/tasks/${t.task_id}/inputs`
        body.question_id = t.question.question_id
        body.answer = { recipients: ["alex@example.test"] }
      } else {
        p = "/assistant/requests"
        if (body.instruction === "Make it shorter")
          body.intent_hint = "summarise"
      }
    }
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
    else if (p === "/assistant/inbox-chat") {
      if (body.instruction === "hey")
        data = { kind: "message", text: "Hey! What can I help you with?" }
      else if (/show.*emails/i.test(body.instruction))
        data = {
          kind: "search",
          search: {
            filters: {
              schema_version: "1.0",
              query: "receipt",
              folder: "all_mail",
              received_from: "2026-01-01T00:00:00Z",
              received_before: "2026-10-01T00:00:00Z"
            },
            // One conversational turn can page Gmail internally and return
            // more than five addressable cards without another UI request.
            results: Array.from({ length: 10 }, (_, i) => ({
              message_id: i ? `msg${i}` : target,
              thread_id: thread,
              subject:
                i === 1
                  ? "Your flight to Melbourne"
                  : i
                    ? `GYG order ${7842 - i}`
                    : "Test receipt",
              sender:
                i === 1 ? "Flights <travel@example.test>" : "Cedar Catering",
              received_at: "2026-09-23T03:00:00Z",
              snippet:
                i === 1
                  ? "Your flight itinerary: JFK → MEL. Flight QF12"
                  : "Thanks for your order. Your receipt and pickup details are inside.",
              flight:
                i === 1
                  ? {
                      origin: "JFK",
                      destination: "MEL",
                      flight_number: "QF12",
                      route_quote: "JFK → MEL",
                      basis: "email_text_not_live_status"
                    }
                  : null
            })),
            next_cursor: "signed-next",
            coverage: { complete: false, page_size: 5 }
          }
        }
      else data = { kind: "continue" }
    } else if (p === "/assistant/inbox-search-page")
      data = {
        filters: body.filters || {
          schema_version: "1.0",
          query: "receipt",
          folder: "all_mail",
          received_from: "2026-01-01T00:00:00Z",
          received_before: "2026-10-01T00:00:00Z"
        },
        results: [
          {
            message_id: "older",
            thread_id: thread,
            subject: "An earlier receipt",
            sender: "Cedar",
            received_at: "2026-09-01T03:00:00Z",
            snippet: "Your earlier order confirmation."
          }
        ],
        next_cursor: null,
        coverage: { complete: false, page_size: 5 }
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
          },
          {
            gmail_msg_id: "msg9",
            from_addr: "supplier@example.test",
            subject: "GYG order 7833",
            body_clean: "Earlier GYG order 7833.",
            sent_at: "2026-09-22T03:00:00Z"
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
    } else if (p === "/assistant/context-snapshots/ctx-1")
      data = {
        thread_id: thread,
        thread_version: 1,
        ui_map: {
          visible_message_ids: [target],
          selected_message_ids: [target]
        }
      }
    else if (p === "/assistant/requests") {
      // Synthetic backend router: UI never selects an intent for a new command.
      const intent =
        body.intent_hint ||
        (/summari[sz]e/i.test(body.instruction)
          ? "summarise"
          : /reply/i.test(body.instruction)
            ? "reply"
            : /write.*email/i.test(body.instruction)
              ? "compose"
              : "other")
      data = resultTask(
        body,
        intent === "summarise"
          ? "summary"
          : ["reply", "compose"].includes(intent)
            ? "draft"
            : "answer"
      )
      data.request = body
      if (["reply", "compose"].includes(intent) && !body.draft_options) {
        data.state = "needs_clarification"
        data.artifact_id = null
        data.question = {
          question_id: "q-1",
          expected_version: 2,
          fields:
            intent === "reply"
              ? ["reply_message_id", "recipients"]
              : ["recipients"],
          prompt: "Provide or select: recipients.",
          expired: false
        }
      }
      if (/meeting time/i.test(body.instruction)) {
        data.state = "unsupported"
        data.artifact_id = null
        data.route = {
          decision: {
            intent: "plan_schedule",
            operations: ["schedule"],
            requested_action: "none"
          }
        }
      }
      if (data.state !== "queued") tasks.set(data.task_id, data)
    } else if (p.endsWith("/inputs")) {
      const t = tasks.get(p.split("/")[3])
      expect(body.question_id).toBe(t.question.question_id)
      const generated = resultTask(
        {
          ...t.request,
          draft_options: {
            to: body.answer.recipients,
            reply_message_id: body.answer.reply_message_id
          }
        },
        "draft"
      )
      const finished = tasks.get(generated.task_id)
      artifacts.get(finished.artifact_id).task_id = t.task_id
      tasks.delete(generated.task_id)
      tasks.set(t.task_id, { ...finished, task_id: t.task_id, question: null })
      data = { ...generated, task_id: t.task_id, question: null }
    } else if (p === "/assistant/tasks")
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
    if (conversational) {
      data = {
        ...(data.task_id
          ? { kind: "task", text: "", task: data }
          : p === "/assistant/inbox-search-page"
            ? { kind: "message", text: "Here are more results.", search: data }
            : data),
        conversation_id: originalTurn.conversation_id,
        version: originalTurn.expected_version + 1
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
  await page.getByRole("button", { name: "Switch to light appearance" }).click()
  await page.getByRole("button", { name: "Close conversation menu" }).click()
  await page.screenshot({
    path: path.join("test-results", "inbox-cards-light.png")
  })
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "Switch to dark appearance" }).click()
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
  await page.getByLabel("Reply to", { exact: true }).selectOption(target)
  await page
    .getByLabel("Email address", { exact: true })
    .fill("supplier@example.test")
  await page.getByRole("button", { name: "Continue request" }).click()
  await page.getByRole("button", { name: "Edit draft" }).click()
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
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "Recent work", exact: true }).click()
  await page
    .getByRole("button", { name: /Draft a reply to this thread/ })
    .click()
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
    path: path.join("test-results", "conversation-narrow-dark.png")
  })
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "Switch to light appearance" }).click()
  await page
    .getByRole("button", { name: "Close conversation menu", exact: true })
    .click()
  await page.screenshot({
    animations: "disabled",
    path: path.join("test-results", "conversation-narrow-light.png")
  })
  await page.setViewportSize({ width: 420, height: 900 })
})
test("settings use real capability and versioned preference contracts; history survives panel reload", async () => {
  await page.reload()
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
  await page.getByRole("button", { name: "Recent work", exact: true }).click()
  await expect(
    page
      .getByRole("heading", { name: "Recent work" })
      .or(page.getByText("Recent work", { exact: true }))
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: /Draft a reply/ }).first()
  ).toBeVisible()
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
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
  await page
    .getByRole("button", { name: "Close settings", exact: true })
    .click()
  await page
    .getByRole("button", { name: "Conversation menu", exact: true })
    .click()
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
