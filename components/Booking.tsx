import { useEffect, useRef, useState } from "react"

import {
  actionReference,
  addresses,
  api,
  errorText,
  requestId
} from "../lib/api"
import type { Artifact } from "../lib/types"

export function Booking({ value }: { value: Artifact }) {
  const c = value.artifact.content
  const [selected, setSelected] = useState<any>(null),
    [offer, setOffer] = useState<any>(null),
    [calendars, setCalendars] = useState<any[]>([]),
    [destination, setDestination] = useState(""),
    [title, setTitle] = useState(""),
    [attendees, setAttendees] = useState(""),
    [location, setLocation] = useState(""),
    [action, setAction] = useState<any>(null),
    [confirmed, setConfirmed] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("")
  useEffect(() => {
    let current = true
    void actionReference(value.artifact_id, "calendar")
      .then(async (id) =>
        id ? api(`/assistant/calendar-actions/${id}`) : null
      )
      .then((a) => {
        if (current && a) setAction(a)
      })
      .catch((e) => {
        if (current) setError(errorText(e))
      })
    return () => {
      current = false
    }
  }, [value.artifact_id])
  const proposalKey = useRef(requestId()),
    approvalKey = useRef(requestId())
  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setError("")
    try {
      await fn()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  useEffect(() => {
    if (!selected || selected.state !== "checking") return
    let live = true
    const timer = setInterval(
      () =>
        api(
          `/calendar/negotiations/${selected.negotiation_id}/selections/${selected.id}`
        )
          .then((r) => {
            if (live) setSelected(r)
          })
          .catch((e) => {
            if (live) setError(errorText(e))
          }),
      2500
    )
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [selected?.id, selected?.state])
  useEffect(() => {
    if (
      !action ||
      ![
        "approved",
        "executing",
        "outcome_unknown",
        "uncertain",
        "reconciling"
      ].includes(action.state)
    )
      return
    let live = true
    const timer = setInterval(
      () =>
        api(`/assistant/calendar-actions/${action.action_id}`)
          .then((r) => {
            if (live) setAction(r)
          })
          .catch(() => {
            if (live)
              setError(
                "Could not check the event status. Refresh this preview; do not create another event."
              )
          }),
      3000
    )
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [action?.action_id, action?.state])
  const choose = (slot: any) =>
    run(async () => {
      const task = await api(`/assistant/tasks/${value.task_id}`)
      const contextId =
        task.effective_context_snapshot_id || task.context_snapshot_id
      if (!contextId)
        throw new Error(
          "Booking requires a selected email thread. These options remain available to copy."
        )
      const ctx = await api(`/assistant/context-snapshots/${contextId}`)
      let current = offer
      if (!current) {
        const n = await api("/calendar/negotiations", {
          request_id: requestId(),
          thread_id: ctx.thread_id,
          expected_thread_version: ctx.thread_version
        })
        current = await api(`/calendar/negotiations/${n.id}/offers`, {
          request_id: requestId(),
          expected_version: n.version,
          expected_thread_version: ctx.thread_version,
          slot_request_id: c.slot_request_id
        })
        setOffer(current)
      }
      const n = await api(`/calendar/negotiations/${current.negotiation_id}`)
      setSelected(
        await api(
          `/calendar/negotiations/${current.negotiation_id}/selections`,
          {
            request_id: requestId(),
            expected_version: n.version,
            offer_id: current.id,
            slot_id: slot.id
          }
        )
      )
      setAction(null)
      const list = await api("/calendar/calendars"),
        prefs = await api("/calendar/preferences")
      setCalendars(
        list.calendars.filter(
          (x) =>
            x.event_write_acl && prefs.preferences.calendar_ids.includes(x.id)
        )
      )
    })
  const format = (v: string, zone: string) =>
    new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: zone
    }).format(new Date(v))
  const editable =
    !action ||
    [
      "proposed",
      "rejected",
      "cancelled",
      "expired",
      "superseded",
      "invalidated"
    ].includes(action.state)
  return (
    <div>
      {c.slots.map((s: any) => (
        <div className="slot" key={s.id}>
          <b>{format(s.start, s.timezone)}</b>
          <span>
            to {format(s.end, s.timezone)} · {s.timezone}
          </span>
          <button
            disabled={
              busy ||
              !editable ||
              value.scheduling_status?.usable === false ||
              new Date(c.expires_at).getTime() < Date.now()
            }
            onClick={() => choose(s)}>
            Select and recheck
          </button>
        </div>
      ))}
      {c.expires_at && (
        <p className="muted">
          Options expire {new Date(c.expires_at).toLocaleString()}.
        </p>
      )}
      {selected && (
        <p role="status">
          Selection: {selected.state.replaceAll("_", " ")}
          {selected.blockers?.length
            ? ` — ${selected.blockers.join(", ").replaceAll("_", " ")}`
            : ""}
          . Selecting a time does not book it.
        </p>
      )}
      {selected?.usable && (
        <fieldset disabled={busy || !editable}>
          <legend>Event details</legend>
          <label>
            Title
            <input
              value={title}
              onChange={(e) => {
                setTitle(e.target.value)
                setAction(null)
              }}
            />
          </label>
          <label>
            Calendar
            <select
              value={destination}
              onChange={(e) => {
                setDestination(e.target.value)
                setAction(null)
              }}>
              <option value="">Choose a calendar</option>
              {calendars.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.summary}
                </option>
              ))}
            </select>
          </label>
          <label>
            Attendees
            <input
              placeholder="Email addresses, separated by commas"
              value={attendees}
              onChange={(e) => {
                setAttendees(e.target.value)
                setAction(null)
              }}
            />
          </label>
          <label>
            Location
            <input
              value={location}
              onChange={(e) => {
                setLocation(e.target.value)
                setAction(null)
              }}
            />
          </label>
          <button
            disabled={!title.trim() || !destination}
            onClick={() =>
              run(async () => {
                proposalKey.current = requestId()
                const list = addresses(attendees)
                const preview = await api(
                  `/assistant/artifacts/${value.artifact_id}/calendar-actions`,
                  {
                    request_id: proposalKey.current,
                    expected_revision: value.revision,
                    selection_id: selected.id,
                    calendar_id: destination,
                    title,
                    location,
                    description: "",
                    attendees: list,
                    send_updates: list.length ? "all" : "none"
                  }
                )
                await actionReference(
                  value.artifact_id,
                  "calendar",
                  preview.action_id
                )
                setAction(preview)
                setConfirmed(false)
                approvalKey.current = requestId()
              })
            }>
            Review event preview
          </button>
        </fieldset>
      )}
      {action && (
        <div className="approval">
          <b>{action.preview.event.summary}</b>
          <p>
            {action.preview.event.start.dateTime} –{" "}
            {action.preview.event.end.dateTime}
            <br />
            {action.preview.event.start.timeZone}
            <br />
            Calendar: {action.preview.calendar_id}
            <br />
            Location: {action.preview.event.location || "Not specified"}
            <br />
            Attendees:{" "}
            {action.preview.event.attendees.map((x) => x.email).join(", ") ||
              "None"}
            <br />
            Invite notifications: {action.preview.send_updates}
          </p>
          <p>Status: {action.state.replaceAll("_", " ")}</p>
          {action.blockers?.length > 0 && (
            <p className="warning">
              {action.blockers.join(", ").replaceAll("_", " ")}
            </p>
          )}
          {action.approval_available && (
            <>
              <label className="check">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(e) => setConfirmed(e.target.checked)}
                />
                Create this exact event and send the listed invitations.
              </label>
              <button
                disabled={!confirmed || busy}
                onClick={() =>
                  run(async () => {
                    setAction(
                      await api(
                        `/assistant/calendar-actions/${action.action_id}/approve`,
                        {
                          request_id: approvalKey.current,
                          expected_version: action.version,
                          payload_hash: action.payload_hash
                        }
                      )
                    )
                    setConfirmed(false)
                  })
                }>
                Approve and create event
              </button>
            </>
          )}
          <button
            disabled={busy}
            onClick={() =>
              run(async () =>
                setAction(
                  await api(`/assistant/calendar-actions/${action.action_id}`)
                )
              )
            }>
            Refresh event status
          </button>
          {action.state === "outcome_unknown" && (
            <p className="warning">
              The provider outcome is uncertain. Threadly is checking it; do not
              create another event.
            </p>
          )}
          {["proposed", "approved"].includes(action.state) && (
            <button
              disabled={busy}
              onClick={() =>
                run(async () => {
                  const r = await api(
                    `/assistant/calendar-actions/${action.action_id}/${action.state === "proposed" ? "reject" : "cancel"}`,
                    {
                      request_id: requestId(),
                      expected_version: action.version
                    }
                  )
                  setAction(r.action || r)
                })
              }>
              {action.state === "proposed"
                ? "Reject preview"
                : "Request cancellation"}
            </button>
          )}
        </div>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  )
}
