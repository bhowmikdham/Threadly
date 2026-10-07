import { useState } from "react"

import { api, errorText, requestId } from "../lib/api"
import type { Artifact, User } from "../lib/types"
import { Booking } from "./Booking"
import { GmailDraftCard } from "./GmailDraftCard"
import { Icon } from "./Icon"

const display = (x: any): string =>
  typeof x === "string" ? x : x?.text || x?.question || x?.description || ""
export function ArtifactCard({
  value,
  replace,
  report,
  draftContext
}: {
  value: Artifact
  replace: (a: Artifact) => void
  report: (s: string) => void
  draftContext?: {
    user: User
    conversation?: { id: string; version: number }
    enabled: boolean
  }
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
    return (
      <GmailDraftCard
        artifact={value}
        user={
          draftContext?.user || {
            id: 0,
            email: value.draft_envelope?.from_address || "",
            name: null
          }
        }
        conversation={draftContext?.conversation}
        enabled={Boolean(draftContext?.enabled) && value.is_latest !== false}
      />
    )
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
