import { useRef, useState } from "react"

import {
  actionReference,
  addresses,
  api,
  errorText,
  requestId
} from "../lib/api"
import type {
  MeetingEmailDraft,
  MeetingEmailSource
} from "../lib/meeting-email"
import type { CalendarAction } from "../lib/types"
import { CalendarEventCard } from "./CalendarEventCard"

import "./MeetingEmailEvent.css"

export function MeetingEmailEvent({
  source,
  disabled
}: {
  source: MeetingEmailSource
  disabled?: boolean
}) {
  const [draft, setDraft] = useState<MeetingEmailDraft>(),
    [action, setAction] = useState<CalendarAction>(),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [title, setTitle] = useState(""),
    [calendar, setCalendar] = useState(""),
    [date, setDate] = useState(""),
    [time, setTime] = useState(""),
    [duration, setDuration] = useState(30),
    [location, setLocation] = useState(""),
    [description, setDescription] = useState(""),
    [guests, setGuests] = useState(""),
    [sendInvites, setSendInvites] = useState(false)
  const working = useRef(false),
    key = useRef(requestId())
  const referenceKey = `meeting-email-${source.thread_id}-${source.message_id}`
  const restartable = [
    "rejected",
    "cancelled",
    "expired",
    "superseded",
    "failed"
  ]
  const run = async (operation: () => Promise<void>) => {
    if (working.current) return
    working.current = true
    setBusy(true)
    setError("")
    try {
      await operation()
    } catch (e) {
      setError(errorText(e))
    } finally {
      working.current = false
      setBusy(false)
    }
  }
  const open = () =>
    run(async () => {
      const previousId = await actionReference(referenceKey, "calendar")
      if (previousId) {
        const previous = await api<CalendarAction>(
          `/assistant/calendar-actions/${previousId}`
        )
        if (!restartable.includes(previous.state)) {
          setAction(previous)
          return
        }
      }
      const result = await api<MeetingEmailDraft>(
        "/calendar/meeting-email/draft",
        { source }
      )
      setDraft(result)
      setTitle(result.title)
      setCalendar(result.calendars.length === 1 ? result.calendars[0].id : "")
      setDuration(result.default_duration_minutes)
      setDate("")
      setTime("")
      setLocation("")
      setDescription("")
      setGuests("")
      setSendInvites(false)
      setAction(undefined)
      key.current = requestId()
    })
  const changed = () => {
    key.current = requestId()
  }
  const preview = () =>
    run(async () => {
      const attendees = addresses(guests)
      if (attendees.length && !sendInvites)
        throw new Error(
          "Confirm invitations for the listed attendees, or remove them."
        )
      const result = await api<CalendarAction>(
        "/calendar/meeting-email/previews",
        {
          request_id: key.current,
          source: draft.source,
          context_snapshot_id: draft.context_snapshot_id,
          expected_preferences_version: draft.preferences_version,
          title,
          calendar_id: calendar,
          date,
          start_time: time,
          duration_minutes: duration,
          location,
          description,
          attendees,
          send_updates: sendInvites ? "all" : "none"
        }
      )
      setAction(result)
      await actionReference(referenceKey, "calendar", result.action_id)
    })
  const edit = () =>
    run(async () => {
      // Retire the exact old candidate before exposing editable fields. A racing
      // approval fails this versioned request instead of silently editing consent.
      await api(`/assistant/calendar-actions/${action.action_id}/reject`, {
        request_id: requestId(),
        expected_version: action.version
      })
      setAction(undefined)
      changed()
    })
  return (
    <div className="meeting-email-event">
      {!draft && !action && (
        <button
          className="text-button"
          disabled={disabled || busy}
          onClick={() => void open()}>
          {busy ? "Reading email…" : "Create event"}
        </button>
      )}
      {draft && !action && (
        <form
          aria-label="Event from email"
          onSubmit={(e) => {
            e.preventDefault()
            void preview()
          }}>
          <h3>Event from email</h3>
          <details>
            <summary>Source email · freshly read</summary>
            <strong>{draft.source_subject || "No subject"}</strong>
            <p>{draft.source_sender}</p>
            <p className="meeting-email-source">{draft.source_excerpt}</p>
            {draft.source_truncated && (
              <small>Only the first part of this email is shown.</small>
            )}
          </details>
          <p className="muted">
            Enter the date and time from this email. Only the title is
            prefilled. Review and confirm before creating the event.
          </p>
          <fieldset disabled={busy || disabled} onChange={changed}>
            <legend>Event details</legend>
            <label>
              Title
              <input
                required
                maxLength={300}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>
            <label>
              Calendar
              <select
                required
                value={calendar}
                onChange={(e) => setCalendar(e.target.value)}>
                <option value="">Choose a calendar</option>
                {draft.calendars.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <p className="muted">Date and time use {draft.timezone}.</p>
            <label>
              Date (required)
              <input
                type="date"
                required
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </label>
            <label>
              Start time (required)
              <input
                type="time"
                required
                value={time}
                onChange={(e) => setTime(e.target.value)}
              />
            </label>
            <label>
              Duration in minutes
              <input
                type="number"
                required
                min={5}
                max={480}
                value={duration}
                onChange={(e) => setDuration(Number(e.target.value))}
              />
            </label>
            <label>
              Location
              <input
                maxLength={500}
                value={location}
                onChange={(e) => setLocation(e.target.value)}
              />
            </label>
            <label>
              Description
              <textarea
                maxLength={4000}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </label>
            <label>
              Attendees (optional)
              <input
                placeholder="Email addresses, separated by commas"
                value={guests}
                onChange={(e) => {
                  setGuests(e.target.value)
                  setSendInvites(false)
                }}
              />
            </label>
            {guests.trim() && (
              <label className="check">
                <input
                  type="checkbox"
                  checked={sendInvites}
                  onChange={(e) => setSendInvites(e.target.checked)}
                />
                Send invitations to the listed attendees when I confirm.
              </label>
            )}
            <button
              className="primary"
              type="submit"
              disabled={
                !title.trim() ||
                !calendar ||
                !date ||
                !time ||
                (Boolean(guests.trim()) && !sendInvites)
              }>
              Review event preview
            </button>
            <button
              type="button"
              onClick={() => {
                setDraft(undefined)
                setError("")
              }}>
              Cancel
            </button>
          </fieldset>
        </form>
      )}
      {action && (
        <>
          <p className="muted">
            Your confirmation is required for this email event, including with
            Always allow enabled.
          </p>
          <CalendarEventCard
            key={action.action_id}
            id={action.action_id}
            initial={action}
            onChange={setAction}
          />
          {draft && action.state === "proposed" && (
            <button disabled={busy || disabled} onClick={() => void edit()}>
              Edit details
            </button>
          )}
          {restartable.includes(action.state) && (
            <button disabled={busy || disabled} onClick={() => void open()}>
              Start a fresh preview
            </button>
          )}
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  )
}
