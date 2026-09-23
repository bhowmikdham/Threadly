import { useState } from "react"

import { addresses } from "../lib/api"
import type { Entry, Selection } from "../lib/types"
import { ArtifactCard } from "./ArtifactCard"

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
        {entry.pending && <p role="status">Preparing your request…</p>}
        {t && (
          <div className="task-status">
            <span role="status">{t.state.replaceAll("_", " ")}</span>
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
          <ol className="steps">
            {progress.steps.map((s) => (
              <li key={s.ordinal}>
                {s.operation.replaceAll("_", " ")} — {s.state}
              </li>
            ))}
          </ol>
        )}
        {t?.state === "failed" && (
          <p role="alert">
            This request could not complete:{" "}
            {t.error_code?.replaceAll("_", " ")}. No successful result is
            implied.
          </p>
        )}
        {t?.state === "unsupported" && !p && (
          <p>
            {t.route?.decision?.rationale ||
              "This request is not supported by the current workflow."}{" "}
            Try the Plan / schedule mode for a reviewed multi-step request.
          </p>
        )}
        {t?.state === "needs_clarification" && (
          <Clarification
            entry={entry}
            selection={controller.selection}
            submit={(values) => controller.answer(entry, values)}
          />
        )}
        {p && p.state !== "consumed" && (
          <section className="proposal">
            <b>Review the complete workflow</b>
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
                Confirm this complete workflow
              </button>
            ) : (
              <p>
                {p.state.replaceAll("_", " ")} — update the full request and
                submit it again.
              </p>
            )}
            <p className="muted">
              This confirmation runs the reviewed steps. Sending and booking
              require a separate exact-content approval.
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
    [values, setValues] = useState<Record<string, string>>({}),
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
      <b>{q.prompt || "A few details are needed"}</b>
      {(q.fields || []).map((field: string) =>
        field === "context_snapshot_id" ? (
          <p key={field}>
            {selection
              ? "Use the selected thread above."
              : "Select a thread above to continue."}
          </p>
        ) : field === "reply_message_id" ? (
          <label key={field}>
            Reply to
            <select
              required
              value={values[field] || ""}
              onChange={(e) =>
                setValues({ ...values, [field]: e.target.value })
              }>
              <option value="">Choose a message</option>
              {selection?.messages.map((m, i) => (
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
            {field.replaceAll("_", " ")}
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
