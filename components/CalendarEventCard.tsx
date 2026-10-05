import { useEffect, useRef, useState } from "react"

import { api, errorText, requestId } from "../lib/api"
import type { CalendarAction } from "../lib/types"
import { GoogleCalendarIcon } from "./Icon"

const status: Record<string, string> = {
  proposed: "Ready to review",
  approved: "Queued",
  executing: "Creating event…",
  succeeded: "Event created",
  outcome_unknown: "Waiting for confirmation",
  failed: "Couldn't create event",
  superseded: "Event needs a fresh check",
  expired: "Preview expired",
  rejected: "Not created",
  cancelled: "Cancelled"
}
export function CalendarEventCard({
  id,
  initial
}: {
  id: string
  initial?: CalendarAction
}) {
  const [action, setAction] = useState(initial),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("")
  const approvalKey = useRef(requestId())
  const path = `/assistant/calendar-actions/${id}`
  const pending =
    !action ||
    ["approved", "executing", "outcome_unknown"].includes(action.state)
  useEffect(() => {
    let live = true,
      timer: ReturnType<typeof setTimeout>
    let rounds = 0
    const load = async () => {
      try {
        const value = await api<CalendarAction>(path)
        if (live) {
          setAction(value)
          setError("")
        }
        if (
          live &&
          pending &&
          ["approved", "executing", "outcome_unknown"].includes(value.state) &&
          ++rounds < 40
        )
          timer = setTimeout(load, 3000)
      } catch {
        if (live)
          setError(
            "Couldn't check this event. Refresh its status before trying to create another."
          )
      }
    }
    void load()
    return () => {
      live = false
      clearTimeout(timer)
    }
  }, [path, pending])
  const act = async (operation?: string) => {
    if (!action && operation) return
    setBusy(true)
    setError("")
    try {
      const result = operation
        ? await api(`${path}/${operation}`, {
            request_id:
              operation === "approve" ? approvalKey.current : requestId(),
            expected_version: action.version,
            ...(operation === "approve"
              ? { payload_hash: action.payload_hash }
              : {})
          })
        : await api(path)
      setAction(result.action || result)
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  const event = action?.preview.event
  const format = (value: string, date = false) =>
    new Intl.DateTimeFormat(undefined, {
      timeZone: event.start.timeZone,
      ...(date
        ? ({
            weekday: "short",
            day: "numeric",
            month: "short",
            year: "numeric"
          } as const)
        : ({ hour: "numeric", minute: "2-digit" } as const))
    }).format(new Date(value))
  return (
    <section className="calendar-event-card" aria-label="Calendar event">
      <div className="calendar-event-heading">
        <GoogleCalendarIcon size={18} />
        <span role="status">
          {action
            ? status[action.state] || "Check event status"
            : "Loading event…"}
        </span>
      </div>
      {event && (
        <>
          <h3>{event.summary}</h3>
          <p>
            {format(event.start.dateTime, true)}
            <br />
            {format(event.start.dateTime)} – {format(event.end.dateTime)}
          </p>
          <p className="muted">
            {event.start.timeZone} ·{" "}
            {action.preview.calendar_name || action.preview.calendar_id}
          </p>
          {event.location && <p>{event.location}</p>}
          {event.description && (
            <p className="calendar-event-description">{event.description}</p>
          )}
          {!!event.attendees.length && (
            <p>
              Invitations to {event.attendees.map((x) => x.email).join(", ")}
            </p>
          )}
          {action.authorization === "chat_permission" && (
            <small className="muted">Using Always allow for this chat</small>
          )}
          {action.state === "proposed" && !!action.blockers.length && (
            <p className="warning">
              This preview needs a fresh Calendar check. Ask me to prepare the
              event again.
            </p>
          )}
          {action.state === "superseded" && (
            <p>
              Settings or availability changed before creation. Ask me to check
              again.
            </p>
          )}
          {action.state === "outcome_unknown" && (
            <p>
              Google hasn't confirmed the result. Threadly is checking; don't
              create a duplicate.
            </p>
          )}
          {action.state === "failed" && (
            <p>
              Check your Calendar connection, then ask me to prepare the event
              again.
            </p>
          )}
          <div className="calendar-event-actions">
            {action.approval_available && (
              <button
                className="primary"
                disabled={busy}
                onClick={() => void act("approve")}>
                {event.attendees.length
                  ? "Create event & send invitations"
                  : "Create event"}
              </button>
            )}
            {["proposed", "approved"].includes(action.state) && (
              <button
                disabled={busy}
                onClick={() =>
                  void act(action.state === "proposed" ? "reject" : "cancel")
                }>
                Cancel
              </button>
            )}
            {action.state !== "succeeded" && (
              <button
                className="text-button"
                disabled={busy}
                onClick={() => void act()}>
                Refresh status
              </button>
            )}
          </div>
        </>
      )}
      {error && <p role="alert">{error}</p>}
      {!action && (
        <button disabled={busy} onClick={() => void act()}>
          Refresh status
        </button>
      )}
    </section>
  )
}
