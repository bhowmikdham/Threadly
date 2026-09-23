import { useEffect, useRef, useState } from "react"

import {
  actionReference,
  addresses,
  api,
  errorText,
  requestId
} from "../lib/api"
import type { Artifact, EmailAction } from "../lib/types"
import { Booking } from "./Booking"

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
      await navigator.clipboard.writeText(c.body || c.overview || c.text || "")
      setNotice("Copied to clipboard.")
    })
  if (artifact.kind === "draft")
    return <DraftCard value={value} replace={replace} />
  return (
    <section className="artifact" aria-label={`${artifact.kind} result`}>
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
              <small>{e.ref_id}</small>
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
  const [body, setBody] = useState(c.body),
    [subject, setSubject] = useState(c.subject),
    [to, setTo] = useState(envelope?.to?.join(", ") || ""),
    [cc, setCc] = useState(envelope?.cc?.join(", ") || ""),
    [bcc, setBcc] = useState(envelope?.bcc?.join(", ") || "")
  const [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [action, setAction] = useState<EmailAction | null>(null),
    [confirmed, setConfirmed] = useState(false),
    [history, setHistory] = useState<any[]>([])
  const [resolved, setResolved] = useState(false),
    key = useRef(requestId()),
    decisionKey = useRef(requestId())
  const dirty =
    body !== c.body ||
    subject !== c.subject ||
    to !== (envelope?.to || []).join(", ") ||
    cc !== (envelope?.cc || []).join(", ") ||
    bcc !== (envelope?.bcc || []).join(", ") ||
    resolved
  useEffect(() => {
    setBody(c.body)
    setSubject(c.subject)
    setTo((envelope?.to || []).join(", "))
    setCc((envelope?.cc || []).join(", "))
    setBcc((envelope?.bcc || []).join(", "))
    setAction(null)
    setConfirmed(false)
    setResolved(false)
    key.current = requestId()
  }, [value.artifact_id])
  useEffect(() => {
    setAction(null)
    setConfirmed(false)
  }, [body, subject, to, cc, bcc, resolved])
  useEffect(() => {
    if (dirty) return
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
  }, [value.artifact_id, dirty])
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
  const save = () =>
    run(async () => {
      const result = await api(
        `/assistant/tasks/${value.task_id}/draft-revisions`,
        {
          request_id: requestId(),
          expected_revision: value.revision,
          subject,
          body,
          recipients: {
            to: addresses(to),
            cc: addresses(cc),
            bcc: addresses(bcc)
          },
          unresolved_fields: resolved ? [] : c.unresolved_fields
        }
      )
      replace(result)
      setNotice("New revision saved. Review it before preparing a send.")
    })
  const prepare = () =>
    run(async () => {
      if (dirty)
        throw new Error("Save your edits before reviewing the outgoing email.")
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
      if (dirty) throw new Error("Save your edits before inserting this draft.")
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
  return (
    <section className="artifact">
      <div className="card-heading">
        <strong>
          {c.mode === "reply" ? "Reply draft" : "New email draft"}
        </strong>
        <span className="muted">Revision {value.revision}</span>
      </div>
      <label>
        To
        <input
          value={to}
          disabled={locked}
          onChange={(e) => setTo(e.target.value)}
        />
      </label>
      <details>
        <summary>Cc and Bcc</summary>
        <label>
          Cc
          <input
            value={cc}
            disabled={locked}
            onChange={(e) => setCc(e.target.value)}
          />
        </label>
        <label>
          Bcc
          <input
            value={bcc}
            disabled={locked}
            onChange={(e) => setBcc(e.target.value)}
          />
        </label>
      </details>
      <label>
        Subject
        <input
          value={subject}
          disabled={locked || !!envelope?.reply}
          onChange={(e) => setSubject(e.target.value)}
        />
      </label>
      <label>
        Message
        <textarea
          aria-label="Message"
          rows={7}
          value={body}
          disabled={locked}
          onChange={(e) => setBody(e.target.value)}
        />
      </label>
      {c.unresolved_fields?.length > 0 && (
        <div className="warning">
          <b>Needs your input</b>
          <ul>
            {c.unresolved_fields.map((x: string) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
          <label className="check">
            <input
              type="checkbox"
              checked={resolved}
              disabled={locked}
              onChange={(e) => setResolved(e.target.checked)}
            />
            I filled in all missing details in this draft.
          </label>
        </div>
      )}
      <div className="button-row">
        <button disabled={!dirty || locked || !value.is_latest} onClick={save}>
          Save edits
        </button>
        <button
          disabled={busy}
          onClick={() =>
            run(async () => {
              await navigator.clipboard.writeText(body)
              setNotice("Copied. Nothing was sent.")
            })
          }>
          Copy
        </button>
        {envelope?.reply && (
          <button disabled={busy || dirty} onClick={insert}>
            Insert body
          </button>
        )}
        <button
          disabled={
            locked ||
            dirty ||
            !value.is_latest ||
            !!value.review?.blockers?.length
          }
          onClick={prepare}>
          Review outgoing email
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
      <details
        onToggle={(e) => {
          if (e.currentTarget.open)
            void api(`/assistant/tasks/${value.task_id}/draft-revisions`)
              .then((r) => setHistory(r.revisions))
              .catch((e) => setNotice(errorText(e)))
        }}>
        <summary>Revision history</summary>
        {history.map((h) => (
          <button
            key={h.artifact_id}
            onClick={() =>
              run(async () =>
                replace(await api(`/assistant/artifacts/${h.artifact_id}`))
              )
            }>
            Revision {h.revision}
          </button>
        ))}
      </details>
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
      <Evidence value={value} />
      {notice && <p role="status">{notice}</p>}
    </section>
  )
}
