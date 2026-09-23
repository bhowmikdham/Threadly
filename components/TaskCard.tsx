import { useState } from "react"

import { addresses } from "../lib/api"
import type { Entry, Selection } from "../lib/types"
import { ArtifactCard } from "./ArtifactCard"
import { Icon } from "./Icon"

export function TaskCard({
  entry,
  controller
}: {
  entry: Entry
  controller: any
}) {
  const t = entry.task,
    p = entry.proposal,
    progress = t?.workflow || t?.compound
  return (
    <article className="exchange">
      <div className="user-message">{entry.instruction}</div>
      <div className="assistant-message">
        {entry.notice && <p className="muted">{entry.notice}</p>}
        {entry.answers?.map((text, i) => (
          <div className="user-message answer-message" key={i}>
            {text}
          </div>
        ))}
        {entry.pending && (
          <p className="thinking" role="status">
            <Icon name="sparkle" size={15} /> Thinking…
          </p>
        )}
        {t && t.state !== "succeeded" && (
          <div className="task-status">
            <span role="status">
              {{
                queued: "Getting started…",
                running: "Working on it…",
                needs_clarification: "One more thing",
                unsupported: "Let’s try another way",
                failed: "Couldn’t finish",
                cancelled: "Stopped"
              }[t.state] || t.state.replaceAll("_", " ")}
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
            I couldn’t finish this request. {t.error_code?.replaceAll("_", " ")}
            .
          </p>
        )}
        {t?.state === "unsupported" && !p && (
          <p>
            {t.route?.decision?.rationale ||
              "This request is not supported by the current workflow."}{" "}
            Describe what you’d like to achieve, including any timing or people
            involved.
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
        {p && p.state !== "consumed" && (
          <section className="proposal">
            <b>Here’s what I’ll do</b>
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
            {p.result?.reason && (
              <p className="warning">{p.result.reason.replaceAll("_", " ")}</p>
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
                {p.state.replaceAll("_", " ")} — update the full request and
                submit it again.
              </p>
            )}
            <p className="muted">
              I’ll prepare the results. You’ll review separately before anything
              is sent or booked.
            </p>
          </section>
        )}
        {entry.artifacts?.map((a) => (
          <ArtifactCard
            key={a.artifact_id}
            value={a}
            replace={(r) => controller.replace(entry.id, r)}
            report={controller.setError}
          />
        ))}
        {entry.error && (
          <p role="alert" className="warning">
            {entry.error}
          </p>
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
    [error, setError] = useState("")
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
