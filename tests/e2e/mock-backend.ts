// Deterministic local stand-in for the Threadly API, shared by the offline
// Playwright suite and `npm run preview`. It never reaches AWS, Gmail or any
// real account: every response below is synthetic fixture data.
import {
  createServer,
  type IncomingMessage,
  type Server,
  type ServerResponse
} from "node:http"

export const target = "abc123",
  thread = "def456"
export const user = { id: 1, email: "tester@example.test", name: "Tester" }

// A realistic thread for `npm run preview`'s stand-in email. The e2e suite
// keeps using the receipt fixture above, so its assertions are unaffected.
export const previewThread = {
  thread: "b7e2a9",
  message: "c41d07",
  from: "Bhowmik Dham <bhowmik@example.test>",
  subject: "Can you review the new Threadly panel?",
  body: "Hey, I've pushed the new conversation panel to the frontend branch. Could you go through the inbox cards and the reply flow and let me know by Thursday if anything needs changing? We're demoing it to the team on Friday. Cheers, Bhowmik",
  sentAt: "2026-09-28T23:15:00Z"
}
const previewReply = [
  "Hi Bhowmik,",
  "Thanks for pushing the panel changes. I'll go through the inbox cards and the reply flow and send you any feedback by Thursday, so we're set for Friday's demo.",
  "Cheers"
]

export type Call = {
  path: string
  body: any
  method: string
  authorized: boolean
}
// Switches a test can flip to exercise failure paths.
export type MockControl = { failLogout: boolean }
export type MockBackend = {
  server: Server
  calls: Call[]
  control: MockControl
}
// Contract checks the fixture makes about incoming requests. The e2e suite
// wires this to Playwright's expect; the preview only logs mismatches.
export type Verify = (label: string, actual: unknown, expected: unknown) => void

export function createMockBackend(verify: Verify = () => {}): MockBackend {
  const control: MockControl = { failLogout: false }
  const calls: Call[] = [],
    tasks = new Map<string, any>(),
    artifacts = new Map<string, any>()
  let lastAction: any = null,
    number = 0
  function resultTask(body: any, kind: string) {
    const preview = body.context_snapshot_id === "ctx-2"
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
        ? preview
          ? {
              overview:
                "Bhowmik has pushed the new conversation panel to the frontend branch and wants your review before Friday's team demo.",
              actions: [
                {
                  text: "Review the inbox cards and reply flow, and send feedback",
                  owner: "You",
                  due_date: "Thursday"
                }
              ],
              decisions: [],
              open_questions: [
                { text: "Does anything need changing before the demo?" }
              ]
            }
          : {
              overview: "Order 7842 totals $18.60. Pickup is at 6:20 PM.",
              actions: [],
              decisions: [],
              open_questions: []
            }
        : kind === "draft"
          ? {
              mode: body.draft_options?.reply_message_id ? "reply" : "new",
              subject: body.draft_options?.reply_message_id
                ? preview
                  ? `Re: ${previewThread.subject}`
                  : "Re: Test receipt"
                : "Project update",
              body:
                body.draft_body ||
                (preview
                  ? previewReply.join("\n\n")
                  : "Thank you for the update."),
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
            : preview
              ? {
                  text: "He'd like your feedback by Thursday; the team demo is on Friday.",
                  found: true
                }
              : { text: "$18.60", found: true }
    artifacts.set(aid, {
      artifact_id: aid,
      task_id: id,
      revision: body.draft_revision || 1,
      is_latest: true,
      artifact: {
        kind,
        content,
        evidence: [
          {
            ref_id: "source-1",
            source_kind: "message",
            source_id: preview ? previewThread.message : target,
            quote: preview
              ? "let me know by Thursday if anything needs changing"
              : "Order 7842 totals $18.60."
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
                ? preview
                  ? {
                      subject: `Re: ${previewThread.subject}`,
                      gmail_message_id: previewThread.message,
                      gmail_thread_id: previewThread.thread
                    }
                  : {
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
  const server = createServer((req, res) =>
    handle(req, res).catch((error) => {
      // Free-form preview input can reach a route the fixture never scripted.
      if (res.headersSent) return res.end()
      res.statusCode = 500
      res.setHeader("Content-Type", "application/json")
      res.setHeader("Access-Control-Allow-Origin", "*")
      res.end(
        JSON.stringify({
          error: { code: "fixture_error", message: String(error) }
        })
      )
    })
  )
  async function handle(req: IncomingMessage, res: ServerResponse) {
    let raw = ""
    for await (const chunk of req) raw += chunk
    const body = raw ? JSON.parse(raw) : undefined
    let p = req.url!.split("?")[0]
    const conversational = p === "/assistant/conversation-turns"
    const originalTurn = conversational ? { ...body } : null
    calls.push({
      path: req.url!,
      body: body ? structuredClone(body) : body,
      method: req.method!,
      authorized: Boolean(req.headers.authorization)
    })
    if (conversational) {
      // A deterministic API fixture. Actual semantic decisions are evaluated against Bedrock.
      if (
        body.instruction === "hey" ||
        (/^(show|find)\b.*emails?\b/i.test(body.instruction) &&
          !/next page/.test(body.instruction))
      )
        p = "/assistant/inbox-chat"
      else if (/next page/.test(body.instruction))
        p = "/assistant/inbox-search-page"
      else if (
        body.instruction === "alex@example.test" &&
        tasks.get(body.active_task_id)?.question
      ) {
        const t = tasks.get(body.active_task_id)
        p = `/assistant/tasks/${t.task_id}/inputs`
        body.question_id = t.question.question_id
        body.answer = { recipients: ["alex@example.test"] }
      } else {
        p = "/assistant/requests"
        if (body.instruction === "Make it shorter")
          body.intent_hint = "summarise"
        // A follow-up while a draft is active revises that draft, as the
        // conversation backend does when the user asks for changes in chat.
        const active = tasks.get(body.active_task_id)
        const draft = active && artifacts.get(active.artifact_id)
        if (
          draft?.artifact.kind === "draft" &&
          !/summari[sz]e|meeting time|how much|^(show|find)\b/i.test(
            body.instruction
          )
        ) {
          const change = body.instruction
            .trim()
            .replace(/^(also |and )?(mention|add|say|include)( that)? /i, "")
            .replace(/^["“'](.*)["”']\.?$/, "$1")
            .replace(/[.!?]*$/, ".")
          body.intent_hint = draft.draft_envelope.reply ? "reply" : "compose"
          body.draft_options = {
            to: draft.draft_envelope.to,
            reply_message_id: draft.draft_envelope.reply?.gmail_message_id
          }
          const line = `${change[0].toUpperCase()}${change.slice(1)}`
          const current = draft.artifact.content.body as string
          body.draft_body = current.endsWith("\n\nCheers")
            ? current.replace(/\n\nCheers$/, `\n\n${line}\n\nCheers`)
            : `Thank you for the update. ${line}`
          body.draft_revision = draft.revision + 1
        }
      }
    }
    if (p === "/auth/google/begin") {
      req.socket.destroy()
      return
    }
    res.setHeader("Content-Type", "application/json")
    res.setHeader("Access-Control-Allow-Origin", "*")
    let data: any
    if (p === "/auth/logout") {
      if (control.failLogout) {
        res.statusCode = 503
        data = {
          error: { code: "service_unavailable", message: "Try again later." }
        }
      } else data = { signed_out: true, scope: "all_sessions" }
    } else if (p === "/auth/refresh") {
      data = {
        jwt:
          "header." +
          Buffer.from(
            JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })
          ).toString("base64url") +
          ".refreshed"
      }
    } else if (p === "/assistant/capabilities")
      data = {
        capabilities: [
          { id: "gmail_read", ready: true },
          { id: "calendar_read", ready: true },
          { id: "calendar_list", ready: true },
          { id: "gmail_send", ready: false, status: "disabled" }
        ]
      }
    else if (p === "/assistant/inbox-chat") {
      if (body.instruction === "hey")
        data = { kind: "message", text: "Hey! What can I help you with?" }
      else if (/^(show|find)\b.*emails?\b/i.test(body.instruction))
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
    else if (p === `/threads/${previewThread.thread}`)
      data = {
        thread: {
          thread_id: previewThread.thread,
          version: 1,
          subject: previewThread.subject
        },
        messages: [
          {
            gmail_msg_id: previewThread.message,
            from_addr: previewThread.from,
            subject: previewThread.subject,
            body_clean: previewThread.body,
            sent_at: previewThread.sentAt
          }
        ]
      }
    else if (
      p === "/assistant/context-snapshots" &&
      body.thread_id === previewThread.thread
    )
      data = {
        context_snapshot_id: "ctx-2",
        thread_id: previewThread.thread,
        thread_version: 1
      }
    else if (p === "/assistant/context-snapshots/ctx-2")
      data = {
        thread_id: previewThread.thread,
        thread_version: 1,
        ui_map: {
          visible_message_ids: [previewThread.message],
          selected_message_ids: [previewThread.message]
        }
      }
    else if (p === "/assistant/context-snapshots") {
      verify(
        "context snapshot selects the target message",
        body.ui_map.selected_message_ids,
        [target]
      )
      verify("context snapshot omits message bodies", "messages" in body, false)
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
      verify(
        "clarification answer targets the open question",
        body.question_id,
        t.question.question_id
      )
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
      data = { tasks: [...tasks.values()].reverse(), next_cursor: null }
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
      verify(
        "plan confirmation echoes plan hash",
        body.plan_hash,
        "c".repeat(64)
      )
      verify(
        "plan confirmation is explicit",
        body.confirm_complete_command,
        true
      )
      data = resultTask(
        { instruction: "Find a meeting time" },
        "schedule_options"
      )
    }
    if (data === undefined) {
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
  }
  return { server, calls, control }
}
