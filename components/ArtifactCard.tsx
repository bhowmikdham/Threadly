import { useEffect, useRef, useState } from "react"

import { actionReference, api, errorText, requestId } from "../lib/api"
import type { Artifact, EmailAction } from "../lib/types"
import { Booking } from "./Booking"
import { Icon } from "./Icon"

const display = (x: any): string =>
  typeof x === "string" ? x : x?.text || x?.question || x?.description || ""
export function ArtifactCard({
  value,
  replace,
  report
}: {
  value: Artifact
  replace: (a: Artifact) => void
  report: (s: string) => void
}) {
  const { artifact } = value,
    c = artifact.content
  const [busy, setBusy] = useState(false),
    [notice, setNotice] = useState("")
  const perform = async (fn: () => Promise<void>) => {
    setBusy(true)
    setNotice("")
    try {
      await fn()
    } catch (e) {
      setNotice(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  const copy = () =>
    perform(async () => {
      await navigator.clipboard.writeText(
        [
          c.body || c.overview || c.text || "",
          ...[
            ["Decisions", c.decisions],
            ["Actions", c.actions],
            ["Open questions", c.open_questions || c.questions]
          ].flatMap(([title, rows]: any) =>
            rows?.length
              ? [
                  `${title}:\n${rows.map((r: any) => "• " + display(r)).join("\n")}`
                ]
              : []
          )
        ]
          .filter(Boolean)
          .join("\n\n")
      )
      setNotice("Copied to clipboard.")
    })
  if (artifact.kind === "draft")
    return <DraftCard value={value} replace={replace} />
  return (
    <section
      className={`artifact result-${artifact.kind}`}
      aria-label={`${artifact.kind} result`}>
      <div className="card-heading">
        <strong>
          {{
            summary: "Summary",
            answer: "Answer",
            plan: "Suggested plan",
            schedule_options: "Meeting options",
            availability: "Availability"
          }[artifact.kind] || "Result"}
        </strong>
        <span className="muted">
          {artifact.coverage === "partial" ? "Selected sources" : ""}
        </span>
      </div>
      {c.overview && <p className="prose">{c.overview}</p>}
      {c.text && <p className="prose">{c.text}</p>}
      {[
        ["Decisions", c.decisions],
        ["Actions", c.actions],
        ["Open questions", c.open_questions || c.questions]
      ].map(([name, rows]: any) =>
        rows?.length ? (
          <div key={name}>
            <b>{name}</b>
            <ul>
              {rows.map((x: any, i: number) => (
                <li key={i}>
                  {display(x)}
                  {x.owner ? ` — ${x.owner}` : ""}
                  {x.due_date ? ` · ${x.due_date}` : ""}
                </li>
              ))}
            </ul>
          </div>
        ) : null
      )}
      {artifact.kind === "plan" && (
        <PlanReview value={value} replace={replace} />
      )}
      {c.items && artifact.kind !== "plan" && (
        <ul>
          {c.items.map((x: any, i: number) => (
            <li key={i}>
              {display(x)}
              {x.value ? `: ${x.value}` : ""}
            </li>
          ))}
        </ul>
      )}
      {c.matches && (
        <ul>
          {c.matches.map((x: any, i: number) => (
            <li key={i}>{x.quote || x.text || x.snippet}</li>
          ))}
        </ul>
      )}
      {c.slots && (
        <>
          <p className="muted">
            Your connected calendars only. Other attendees’ availability is
            unknown. These times are not reserved.
          </p>
          <Booking value={value} />
        </>
      )}
      <Evidence value={value} />
      {(c.text || c.overview) && (
        <button disabled={busy} onClick={copy}>
          <Icon name="copy" size={14} />
          Copy
        </button>
      )}
      {notice && <p role="status">{notice}</p>}
    </section>
  )
}
function Evidence({ value }: { value: Artifact }) {
  return (
    <>
      {value.artifact.evidence?.length > 0 && (
        <details>
          <summary>Sources ({value.artifact.evidence.length})</summary>
          {value.artifact.evidence.map((e: any, i: number) => (
            <div key={e.ref_id || i} className="source">
              <span>
                {e.source_kind === "message"
                  ? "Selected email"
                  : e.source_kind?.replaceAll("_", " ")}
              </span>
              {e.quote && <blockquote className="prose">{e.quote}</blockquote>}
            </div>
          ))}
        </details>
      )}
      {value.artifact.assumptions?.length > 0 && (
        <details>
          <summary>Scope and limitations</summary>
          <ul>
            {value.artifact.assumptions.map((x, i) => (
              <li key={i}>{display(x)}</li>
            ))}
          </ul>
        </details>
      )}
    </>
  )
}
function PlanReview({
  value,
  replace
}: {
  value: Artifact
  replace: (a: Artifact) => void
}) {
  const items = value.artifact.content.items || [],
    [selected, setSelected] = useState<string[]>(
      value.artifact.content.accepted_item_ids || []
    ),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false)
  return (
    <div>
      {items.map((item: any) => (
        <label className="check" key={item.id}>
          <input
            type="checkbox"
            checked={selected.includes(item.id)}
            onChange={(e) =>
              setSelected(
                e.target.checked
                  ? [...selected, item.id]
                  : selected.filter((id) => id !== item.id)
              )
            }
          />
          <span>
            {item.text}
            {item.owner && ` — ${item.owner}`}
            {item.due_date && ` · ${item.due_date}`}
          </span>
        </label>
      ))}
      <p className="muted">
        Select the commitments you accept, including their dependencies. This
        does not mark them completed.
      </p>
      <button
        disabled={busy || !selected.length}
        onClick={async () => {
          setBusy(true)
          try {
            replace(
              await api(`/assistant/tasks/${value.task_id}/plan-review`, {
                request_id: requestId(),
                expected_revision: value.revision,
                accepted_item_ids: selected
              })
            )
            setError("Selected commitments saved.")
          } catch (e) {
            setError(errorText(e))
          } finally {
            setBusy(false)
          }
        }}>
        Accept selected items
      </button>
      {error && <p role="status">{error}</p>}
    </div>
  )
}
function DraftCard({
  value,
  replace
}: {
  value: Artifact
  replace: (a: Artifact) => void
}) {
  const c = value.artifact.content,
    envelope = value.draft_envelope
  // Drafts are read-only here: changes are asked for in the chat and arrive
  // from the backend as a new revision.
  const body = c.body,
    subject = c.subject,
    to = (envelope?.to || []).join(", ")
  const [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [action, setAction] = useState<EmailAction | null>(null),
    [confirmed, setConfirmed] = useState(false),
    [replyOpen, setReplyOpen] = useState(false)
  const key = useRef(requestId()),
    decisionKey = useRef(requestId())
  useEffect(() => {
    setAction(null)
    setConfirmed(false)
    key.current = requestId()
  }, [value.artifact_id])
  useEffect(() => {
    let current = true
    void actionReference(value.artifact_id, "email")
      .then(async (id) => (id ? api(`/assistant/actions/${id}`) : null))
      .then((a) => {
        if (current && a) setAction(a)
      })
      .catch((e) => {
        if (current) setNotice(errorText(e))
      })
    return () => {
      current = false
    }
  }, [value.artifact_id])
  useEffect(() => {
    if (
      !action ||
      ![
        "approved",
        "queued",
        "executing",
        "outcome_unknown",
        "uncertain",
        "reconciling"
      ].includes(action.state)
    )
      return
    let active = true
    const timer = setInterval(
      () =>
        api(`/assistant/actions/${action.action_id}`)
          .then((a) => {
            if (active) setAction(a)
          })
          .catch(() => {
            if (active)
              setNotice(
                "Could not refresh the sending status. Use Refresh status; do not send another copy."
              )
          }),
      3000
    )
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [action?.action_id, action?.state])
  // Insert is only offered while Gmail shows a reply box for this thread.
  useEffect(() => {
    const threadId = envelope?.reply?.gmail_thread_id
    if (!threadId || !chrome.tabs?.query) return
    let active = true
    const check = async () => {
      try {
        const [tab] = await chrome.tabs.query({
          active: true,
          lastFocusedWindow: true
        })
        const open =
          !!tab?.id &&
          !!tab.url?.startsWith("https://mail.google.com/") &&
          (await chrome.tabs
            .sendMessage(tab.id, { action: "THREADLY_SELECTION" })
            .then(
              (s: any) => s?.replyEditorOpen === true && s.threadId === threadId
            ))
        if (active) setReplyOpen(open)
      } catch {
        if (active) setReplyOpen(false)
      }
    }
    void check()
    const timer = setInterval(check, 1500)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [envelope?.reply?.gmail_thread_id])
  const run = async (fn: () => Promise<void>) => {
    if (busy) return
    setBusy(true)
    setNotice("")
    try {
      await fn()
    } catch (e) {
      setNotice(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  const prepare = () =>
    run(async () => {
      const reviewed = await api(
        `/assistant/artifacts/${value.artifact_id}/review`,
        {
          expected_revision: value.revision,
          payload_hash: value.review.payload_hash
        }
      )
      replace(reviewed)
      const a = await api(`/assistant/artifacts/${value.artifact_id}/actions`, {
        request_id: key.current,
        expected_revision: value.revision,
        action_type: "send_email"
      })
      await actionReference(value.artifact_id, "email", a.action_id)
      setAction(a)
      setConfirmed(false)
      decisionKey.current = requestId()
    })
  const insert = () =>
    run(async () => {
      const [tab] = await chrome.tabs.query({
        active: true,
        lastFocusedWindow: true
      })
      if (!tab?.id || !tab.url?.startsWith("https://mail.google.com/"))
        throw new Error("Open the selected Gmail thread first.")
      const reply = envelope?.reply
      const result = await chrome.tabs.sendMessage(tab.id, {
        action: "THREADLY_INSERT",
        threadId: reply.gmail_thread_id,
        messageId: reply.gmail_message_id,
        body: c.body
      })
      if (!result?.ok)
        throw new Error(result?.message || "Could not insert the draft.")
      setNotice(
        "Body inserted into Gmail’s reply editor. Gmail may autosave it as a draft; nothing was sent."
      )
    })
  const locked =
    busy ||
    (!!action &&
      ![
        "proposed",
        "rejected",
        "cancelled",
        "expired",
        "superseded",
        "invalidated"
      ].includes(action.state))
  const copyDraft = () =>
    run(async () => {
      await navigator.clipboard.writeText(body)
      setNotice("Copied. Nothing was sent.")
    })
  // The draft reads as the reply itself: its text, with Insert and Copy.
  // Changes are asked for in the chat; the send review sits underneath.
  return (
    <section className="artifact draft-card" aria-label="draft result">
      <div className="draft-box">
        {!envelope?.reply && (
          <p className="draft-meta">
            <span>To {to || "—"}</span>
            {subject && <span>{subject}</span>}
          </p>
        )}
        <p className="prose draft-body">{body}</p>
        {c.unresolved_fields?.length > 0 && (
          <div className="warning">
            <b>Needs your input</b>
            <ul>
              {c.unresolved_fields.map((x: string) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
            <p>Tell Threadly the missing details in the chat.</p>
          </div>
        )}
        <div className="draft-actions">
          {envelope?.reply && (
            // Ready once Gmail's reply box is open for this thread.
            <button disabled={busy || !replyOpen} onClick={insert}>
              Insert
            </button>
          )}
          <button disabled={busy} onClick={copyDraft}>
            Copy
          </button>
        </div>
        {notice && (
          <p className="draft-notice" role="status">
            {notice}
          </p>
        )}
      </div>
      <div className="draft-footer">
        <button
          className="icon-button"
          aria-label="Review outgoing email"
          title="Review and send"
          disabled={
            locked || !value.is_latest || !!value.review?.blockers?.length
          }
          onClick={prepare}>
          <Icon name="shield" size={15} />
        </button>
      </div>
      {value.review?.blockers?.length > 0 && (
        <p className="warning">
          Review blocked:{" "}
          {value.review.blockers.join(", ").replaceAll("_", " ")}.
        </p>
      )}
      {!value.is_latest && (
        <button
          onClick={() =>
            run(async () =>
              replace(
                await api(`/assistant/artifacts/${value.latest_artifact_id}`)
              )
            )
          }>
          Load latest revision
        </button>
      )}
      {action && (
        <div className="approval">
          <b>Exact outgoing email</b>
          <p>
            From: {action.preview.from_address}
            <br />
            To: {action.preview.to.join(", ")}
            {action.preview.cc.length > 0 && (
              <>
                <br />
                Cc: {action.preview.cc.join(", ")}
              </>
            )}
            {action.preview.bcc.length > 0 && (
              <>
                <br />
                Bcc: {action.preview.bcc.join(", ")}
              </>
            )}
          </p>
          <b>{action.preview.subject}</b>
          <p className="prose">{action.preview.body}</p>
          <p>Status: {action.state.replaceAll("_", " ")}</p>
          {action.blockers.length > 0 && (
            <p className="warning">
              {action.blockers.join(", ").replaceAll("_", " ")}
            </p>
          )}
          {action.recovery && <p>{action.recovery.guidance}</p>}
          {action.approval_available && action.sending_available && (
            <>
              <label className="check">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(e) => setConfirmed(e.target.checked)}
                />
                I approve sending this exact email to these recipients.
              </label>
              <button
                className="primary"
                disabled={!confirmed || busy}
                onClick={() =>
                  run(async () => {
                    const r = await api(
                      `/assistant/actions/${action.action_id}/approve`,
                      {
                        request_id: decisionKey.current,
                        expected_version: action.version,
                        payload_hash: action.payload_hash
                      }
                    )
                    setAction(r.action)
                    setConfirmed(false)
                  })
                }>
                Approve and send
              </button>
            </>
          )}
          <button
            disabled={busy}
            onClick={() =>
              run(async () =>
                setAction(await api(`/assistant/actions/${action.action_id}`))
              )
            }>
            Refresh status
          </button>
          {action.allowed_operations
            .filter((op) => ["reject", "cancel"].includes(op))
            .map((op) => (
              <button
                key={op}
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    const r = await api(
                      `/assistant/actions/${action.action_id}/${op}`,
                      {
                        request_id: requestId(),
                        expected_version: action.version
                      }
                    )
                    setAction(r.action)
                  })
                }>
                {op === "reject" ? "Reject preview" : "Request cancellation"}
              </button>
            ))}
        </div>
      )}
    </section>
  )
}
