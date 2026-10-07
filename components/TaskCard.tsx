import { useEffect, useRef, useState } from "react"

import { addresses } from "../lib/api"
import { mailExcerpt, mailText } from "../lib/mail-display"
import { needsCalendarSetup } from "../lib/scheduling-readiness"
import type { Entry, Selection } from "../lib/types"
import { ArtifactCard } from "./ArtifactCard"
import { AssistantText } from "./AssistantText"
import { CalendarAvailabilityCard } from "./CalendarAvailabilityCard"
import { CalendarChoiceCard } from "./CalendarChoiceCard"
import { CalendarEventCard } from "./CalendarEventCard"
import { GmailDraftCard } from "./GmailDraftCard"
import { Icon } from "./Icon"
import { InboxCards, VISIBLE_MAIL_BATCH } from "./InboxCards"

const senderAddress = (from?: string | null) => {
  const candidate = from?.match(/<([^>]+)>/)?.[1] || from || ""
  return /^[^\s<>@]+@[^\s<>@]+$/.test(candidate) ? candidate : ""
}
// A reply question can be answered without asking when the attached email pins
// down both parts: the message the user opened, and a real sender address. Any
// other question, or any doubt about the target, still shows the form.
export function knownReplyAnswer(entry: Entry, selection: Selection | null) {
  const fields = entry.task?.question?.fields || []
  if (
    !fields.length ||
    entry.task?.question?.expired ||
    fields.some((f) => !["reply_message_id", "recipients"].includes(f))
  )
    return null
  const reply =
    fields.includes("reply_message_id") ||
    entry.task?.route?.decision?.intent === "reply"
  const target = selection?.targetId
  const message = selection?.messages.find((m) => m.gmail_msg_id === target)
  if (!reply || !message || !selection.selectedIds.includes(target)) return null
  const recipient = senderAddress(message.from_addr)
  if (fields.includes("recipients") && !recipient) return null
  const answer: Record<string, any> = {}
  if (fields.includes("reply_message_id")) answer.reply_message_id = target
  if (fields.includes("recipients")) answer.recipients = [recipient]
  return answer
}
// Rotating status words, so a wait reads as progress rather than a stall.
const phrases = {
  thinking: [
    "Thinking it through…",
    "Reading the thread…",
    "Analysing…",
    "Putting it together…"
  ],
  working: [
    "Getting started…",
    "Analysing…",
    "Working on it…",
    "Checking the details…"
  ],
  reply: [
    "Reading the thread…",
    "Drafting your reply…",
    "Checking the details…"
  ]
}
function Working({ kind }: { kind: keyof typeof phrases }) {
  const [i, setI] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setI((n) => n + 1), 1800)
    return () => clearInterval(id)
  }, [])
  const list = phrases[kind]
  return <>{list[i % list.length]}</>
}
export function TaskCard({
  entry,
  controller
}: {
  entry: Entry
  controller: any
}) {
  const t = entry.task,
    p = entry.proposal,
    progress = t?.workflow || t?.compound,
    autoReply =
      t?.state === "needs_clarification" &&
      !entry.error &&
      knownReplyAnswer(entry, controller.selection),
    calendarSetupNeeded = [
      entry.errorCode,
      t?.error_code,
      p?.result?.reason,
      t?.route?.decision?.reason
    ].some(needsCalendarSetup)
  // The selectable card is already the source for these exact excerpts. Keep
  // distinct body evidence and evidence without an explicitly matching ref.
  // Only the first batch is guaranteed visible; retain later-page citations.
  const visibleCards = Array.isArray(entry.inbox?.results)
    ? entry.inbox.results.slice(0, VISIBLE_MAIL_BATCH)
    : []
  const evidence = entry.evidence?.filter((item) => {
    const quote = mailText(item.quote).replace(/\s+/g, " ")
    return (
      // Isolated joining/directional marks are invisible, but remain intact
      // inside meaningful words. A blank quote must not create a Sources box.
      Boolean(quote.replace(/[\u200c-\u200f]/g, "").trim()) &&
      !visibleCards.some(
        (mail) =>
          mail &&
          (mail.reference === item.reference ||
            mail.message_id === item.reference) &&
          [
            mailText(mail.sender),
            mailText(mail.subject),
            mailExcerpt(mail.snippet)
          ].some((text) => text.includes(quote))
      )
    )
  })
  return (
    <article className="exchange" data-entry-id={entry.id}>
      {entry.instruction && (
        <div className="user-message">{entry.instruction}</div>
      )}
      <div className="assistant-message">
        {entry.message &&
          !entry.emailDraft &&
          !entry.calendarActionId &&
          (!p || p.state === "proposed" || p.state === "consumed") && (
            <AssistantText text={entry.message} />
          )}
        {evidence?.length > 0 && (
          <details className="work-details">
            <summary>Sources</summary>
            {evidence.map((e, i) => (
              <blockquote key={i}>{mailText(e.quote)}</blockquote>
            ))}
          </details>
        )}
        {entry.error && !t && controller.canRetry?.(entry) && (
          <button onClick={() => controller.retry(entry)}>
            Retry response
          </button>
        )}
        {entry.emailDraft && (
          <GmailDraftCard
            key={entry.emailDraft.draft_id}
            draft={entry.emailDraft}
            user={controller.user}
            conversation={{
              id: entry.conversationId,
              version: entry.conversationVersion
            }}
            enabled={Boolean(controller.canCreateDraft?.(entry))}
            replyThreadId={controller.selection?.thread.thread_id}
          />
        )}
        {entry.calendarAvailability && (
          <CalendarAvailabilityCard
            value={entry.calendarAvailability}
            enabled={Boolean(controller.canRecheckAvailability?.(entry))}
            onRecheck={() => void controller.recheckAvailability(entry)}
          />
        )}
        {entry.calendarActionId && (
          <CalendarEventCard
            id={entry.calendarActionId}
            initial={entry.calendarAction}
          />
        )}
        {entry.calendarChoices && (
          <CalendarChoiceCard
            value={entry.calendarChoices}
            enabled={Boolean(controller.canChooseCalendar?.(entry))}
            choose={(choiceId) =>
              void controller.chooseCalendar(entry, choiceId)
            }
          />
        )}
        {entry.inbox && <InboxCards entry={entry} controller={controller} />}
        {entry.notice && <p className="muted">{entry.notice}</p>}
        {entry.answers?.map((text, i) => (
          <div className="user-message answer-message" key={i}>
            {text}
          </div>
        ))}
        {entry.pending && (
          <p className="thinking" role="status">
            <Icon name="sparkle" size={15} /> <Working kind="thinking" />
          </p>
        )}
        {t && t.state !== "succeeded" && !autoReply && (
          <div className="task-status">
            <span role="status">
              {["queued", "running"].includes(t.state) ? (
                <Working kind="working" />
              ) : (
                {
                  needs_clarification: "One more thing",
                  unsupported: "Let’s try another way",
                  failed: "Couldn’t finish",
                  cancelled: "Stopped"
                }[t.state] || t.state.replaceAll("_", " ")
              )}
            </span>
            {progress?.total_steps > 0 && (
              <>
                <progress
                  aria-label="Task progress"
                  value={progress.completed_steps}
                  max={progress.total_steps}
                />
                <small>
                  {progress.completed_steps} of {progress.total_steps} steps
                </small>
              </>
            )}
            {["queued", "running"].includes(t.state) && (
              <button onClick={() => controller.cancel(entry)}>
                Cancel task
              </button>
            )}
            {entry.error && (
              <button onClick={() => controller.resume(entry)}>
                Refresh task status
              </button>
            )}
          </div>
        )}
        {progress?.steps?.length > 0 && (
          <details className="work-details">
            <summary>Steps in this request</summary>
            <ol className="steps">
              {progress.steps.map((s) => (
                <li key={s.ordinal}>
                  {s.operation.replaceAll("_", " ")} — {s.state}
                </li>
              ))}
            </ol>
          </details>
        )}
        {t?.state === "failed" && (
          <p role="alert">
            {needsCalendarSetup(t.error_code)
              ? "Calendar needs setup before this scheduling request can continue."
              : `I couldn’t finish this request. ${t.error_code?.replaceAll("_", " ")}.`}
          </p>
        )}
        {t?.state === "unsupported" && !p && (
          <p>
            I can’t complete that request as written. What would you like to do
            next?
          </p>
        )}
        {t?.state === "needs_clarification" && (
          <Clarification
            key={t.question?.question_id || entry.id}
            entry={entry}
            selection={controller.selection}
            submit={(values) => controller.answer(entry, values)}
          />
        )}
        {p && ["failed", "expired"].includes(p.state) && (
          <section className="proposal" role="alert">
            <b>
              {p.state === "expired"
                ? "This request has expired"
                : "I couldn’t prepare this request"}
            </b>
            <p>
              {p.state === "expired"
                ? "Ask again to get an up-to-date result."
                : "Something went wrong while preparing the result. You can try the same request again."}
            </p>
            <p className="muted">Nothing was sent or booked.</p>
            <button
              disabled={entry.pending || controller.busy}
              onClick={() => controller.submit(entry.instruction)}>
              Try request again
            </button>
          </section>
        )}
        {p && !["consumed", "failed", "expired"].includes(p.state) && (
          <section className="proposal">
            <b>
              {p.state === "proposed"
                ? "Here’s what I’ll do"
                : p.state === "planning"
                  ? "Preparing your request"
                  : "A little more detail is needed"}
            </b>
            <p>{p.request.instruction}</p>
            <ol>
              {p.result?.clauses?.map((c: any, i: number) => (
                <li key={i}>
                  <b>
                    {c.kind === "prohibited"
                      ? "Do not: "
                      : c.kind === "context"
                        ? "Context: "
                        : ""}
                  </b>
                  {c.text}
                </li>
              ))}
            </ol>
            {p.result?.questions?.map((q: string) => <p key={q}>{q}</p>)}
            {p.result?.reason && p.state === "unsupported" && (
              <p className="warning">
                I couldn’t prepare all of the requested steps. Please revise the
                request.
              </p>
            )}
            {p.result?.scheduling_assumptions?.map((x: any, i: number) => (
              <p key={i}>
                {typeof x === "string" ? x : x.text || x.description}
              </p>
            ))}
            {p.state === "proposed" ? (
              <button
                className="primary"
                disabled={
                  entry.pending || new Date(p.expires_at).getTime() < Date.now()
                }
                onClick={() => controller.confirm(entry)}>
                Continue with these steps
              </button>
            ) : (
              <p>
                {p.state === "planning"
                  ? "The request is still being prepared."
                  : "Add the missing details and submit your request again."}
              </p>
            )}
            {p.state === "proposed" && (
              <p className="muted">
                I’ll prepare the results. You’ll review separately before
                anything is sent or booked.
              </p>
            )}
          </section>
        )}
        {entry.artifacts?.map((a) => (
          <ArtifactCard
            key={a.artifact_id}
            value={a}
            replace={(r) => controller.replace(entry.id, r)}
            report={controller.setError}
            draftContext={{
              user: controller.user,
              conversation: entry.conversationId
                ? {
                    id: entry.conversationId,
                    version: entry.conversationVersion
                  }
                : undefined,
              enabled: Boolean(controller.canCreateDraft?.(entry))
            }}
          />
        ))}
        {!entry.pending &&
          (entry.message || entry.inbox || entry.artifacts?.length > 0) && (
            <div className="response-footer">
              {(entry.message || entry.inbox) && (
                <button
                  className="icon-button"
                  aria-label="Copy response"
                  title="Copy response"
                  onClick={() =>
                    void navigator.clipboard
                      .writeText(
                        entry.message ||
                          entry.inbox.results
                            .map(
                              (m) =>
                                `${mailText(m.subject)}\n${mailExcerpt(m.snippet)}`
                            )
                            .join("\n\n")
                      )
                      .catch((e) => controller.setError(e.message))
                  }>
                  <Icon name="copy" size={15} />
                </button>
              )}
              {entry.createdAt && (
                <time
                  dateTime={entry.createdAt}
                  title={new Date(entry.createdAt).toLocaleString()}>
                  {new Date(entry.createdAt).toLocaleTimeString(undefined, {
                    hour: "numeric",
                    minute: "2-digit"
                  })}
                </time>
              )}
            </div>
          )}
        {entry.error && (
          <p role="alert" className="warning">
            {entry.error}
          </p>
        )}
        {calendarSetupNeeded && controller.openCalendarSetup && (
          <div className="calendar-recovery">
            <button onClick={controller.openCalendarSetup}>
              Review calendars
            </button>
          </div>
        )}
      </div>
    </article>
  )
}
function Clarification({
  entry,
  selection,
  submit
}: {
  entry: Entry
  selection: Selection | null
  submit: (v: any) => void
}) {
  const q = entry.task.question,
    [values, setValues] = useState<Record<string, string>>(() => {
      const selected = selection?.messages.find(
        (m) => m.gmail_msg_id === selection.targetId
      )
      const candidate =
        selected?.from_addr?.match(/<([^>]+)>/)?.[1] ||
        selected?.from_addr ||
        ""
      return {
        reply_message_id: selection?.targetId || "",
        recipients:
          (q.fields?.includes("reply_message_id") ||
            entry.task.route?.decision?.intent === "reply") &&
          /^[^\s<>@]+@[^\s<>@]+$/.test(candidate)
            ? candidate
            : ""
      }
    }),
    [error, setError] = useState(""),
    known = entry.error ? null : knownReplyAnswer(entry, selection),
    sent = useRef(false)
  useEffect(() => {
    if (known && !entry.pending && !sent.current) {
      sent.current = true
      submit(known)
    }
  }, [])
  if (known)
    return (
      <p className="thinking" role="status">
        <Icon name="sparkle" size={15} /> <Working kind="reply" />
      </p>
    )
  if (!q)
    return (
      <p>
        {entry.task.route?.decision?.clarification ||
          "Add the missing details and submit the complete request again."}
      </p>
    )
  return (
    <form
      className="clarification"
      onSubmit={(e) => {
        e.preventDefault()
        try {
          const v: any = {}
          for (const field of q.fields || []) {
            if (field === "context_snapshot_id") {
              v[field] = "selected"
              continue
            }
            const x = values[field]
            if (x)
              v[field] =
                field === "recipients"
                  ? addresses(x)
                  : ["duration_minutes", "fold"].includes(field)
                    ? Number(x)
                    : x
          }
          submit(v)
        } catch (e) {
          setError(e.message)
        }
      }}>
      <b>
        {q.fields?.length === 1 && q.fields[0] === "recipients"
          ? "Who should this go to?"
          : q.fields?.includes("reply_message_id")
            ? "Which message are you replying to?"
            : q.prompt?.startsWith("Provide or select:")
              ? "I need a little more detail to continue."
              : q.prompt || "One more thing…"}
      </b>
      {(q.fields || []).map((field: string) =>
        field === "context_snapshot_id" ? (
          <p key={field}>
            {selection
              ? "Use the email attached to this conversation."
              : "Attach an email with the + button to continue."}
          </p>
        ) : field === "reply_message_id" ? (
          <label key={field}>
            Reply to
            <select
              aria-label="Reply to"
              required
              value={values[field] || ""}
              onChange={(e) =>
                setValues({ ...values, [field]: e.target.value })
              }>
              <option value="">Choose a message</option>
              {selection?.messages
                .filter((m) => selection.selectedIds.includes(m.gmail_msg_id))
                .map((m, i) => (
                  <option key={m.gmail_msg_id} value={m.gmail_msg_id}>
                    Message {i + 1} · {m.from_addr}
                  </option>
                ))}
            </select>
          </label>
        ) : ["am_or_pm", "meridiem"].includes(field) ? (
          <label key={field}>
            Time of day
            <select
              required
              value={values[field] || ""}
              onChange={(e) =>
                setValues({ ...values, [field]: e.target.value })
              }>
              <option value="">Select AM or PM</option>
              <option>AM</option>
              <option>PM</option>
            </select>
          </label>
        ) : (
          <label key={field}>
            {{
              recipients: "Email address",
              timezone: "Timezone",
              duration_minutes: "Meeting length (minutes)",
              date_phrase: "Day or date",
              time_phrase: "Time"
            }[field] || field.replaceAll("_", " ")}
            <input
              required
              value={values[field] || ""}
              type={
                field === "duration_minutes" || field === "fold"
                  ? "number"
                  : "text"
              }
              onChange={(e) =>
                setValues({ ...values, [field]: e.target.value })
              }
            />
          </label>
        )
      )}
      <button disabled={entry.pending || q.expired}>Continue request</button>
      {q.expired && <p>This question expired. Submit a fresh request.</p>}
      {error && <p role="alert">{error}</p>}
    </form>
  )
}
