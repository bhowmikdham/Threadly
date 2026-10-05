import { useState } from "react"

import { api, bridge, errorText } from "../lib/api"
import {
  calendarEventsReadReady,
  type PreferencesState
} from "../lib/scheduling-readiness"
import type { Capability } from "../lib/types"

type AgendaEvent = {
  summary: string
  start: string
  end: string
  all_day: boolean
  location?: string | null
}
type Agenda = {
  timezone: string
  start: string
  end: string
  coverage: "complete" | "partial" | "unknown"
  checked_at: string
  total_returned: number
  calendars: {
    calendar_id: string
    name: string
    status: "known" | "partial" | "unknown"
    reason?: string | null
    events: AgendaEvent[]
  }[]
}
const periods = [
  ["today", "Today"],
  ["tomorrow", "Tomorrow"],
  ["this_week", "This week"],
  ["next_7_days", "Next 7 days"]
] as const
type Period = (typeof periods)[number][0]
const coverageReason: Record<string, string> = {
  not_accessible: "Access to this calendar is unavailable",
  provider_error: "Events could not be read right now",
  result_limit: "More events exist than can be shown"
}

export function CalendarAgenda({
  capabilities,
  preferencesState,
  onAuth
}: {
  capabilities: Capability[]
  preferencesState: PreferencesState
  onAuth: () => Promise<void>
}) {
  const [period, setPeriod] = useState<Period>("today")
  const [agenda, setAgenda] = useState<Agenda | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [reconnect, setReconnect] = useState(false)
  const canRead = calendarEventsReadReady(capabilities) && !reconnect
  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setError("")
    try {
      await fn()
    } catch (cause) {
      setError(errorText(cause))
      if (cause?.code === "calendar_events_access_required") setReconnect(true)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="calendar-agenda" aria-label="Upcoming Calendar events">
      <h4>See upcoming events</h4>
      <p className="muted">Event details from your selected calendars.</p>
      {!canRead ? (
        <button
          disabled={busy}
          onClick={() =>
            void run(async () => {
              await bridge({
                type: "LOGIN",
                capabilities: ["calendar_events_read"]
              })
              await onAuth()
              setReconnect(false)
            })
          }>
          {busy ? "Connecting…" : "Allow event details"}
        </button>
      ) : preferencesState !== "ready" ? (
        <p className="muted">
          Save your selected calendars and timezone above to see events.
        </p>
      ) : (
        <>
          <label>
            Show events for
            <select
              value={period}
              onChange={(event) => {
                setPeriod(event.target.value as Period)
                setAgenda(null)
              }}>
              {periods.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <button
            disabled={busy}
            onClick={() =>
              void run(async () => {
                setAgenda(
                  await api<Agenda>(`/calendar/agenda?period=${period}`)
                )
              })
            }>
            {busy ? "Checking…" : "View events"}
          </button>
          {agenda && (
            <div className="agenda-results" role="status">
              <p>
                {agenda.coverage === "complete"
                  ? `Events in ${agenda.timezone}`
                  : `Some events may be missing.`}
              </p>
              {agenda.calendars.map((calendar) => (
                <div key={calendar.calendar_id}>
                  {calendar.status !== "known" && (
                    <p className="muted">
                      {calendar.name}:{" "}
                      {coverageReason[calendar.reason || ""] ||
                        "Events unavailable"}
                    </p>
                  )}
                  {calendar.events.map((event, index) => (
                    <article
                      className="agenda-event"
                      key={`${calendar.calendar_id}-${index}`}>
                      <strong>{event.summary}</strong>
                      <small>
                        {event.all_day
                          ? "All day"
                          : new Intl.DateTimeFormat(undefined, {
                              dateStyle: "medium",
                              timeStyle: "short",
                              timeZone: agenda.timezone
                            }).format(new Date(event.start))}
                        {event.location ? ` · ${event.location}` : ""}
                      </small>
                    </article>
                  ))}
                </div>
              ))}
              {agenda.coverage === "complete" &&
                agenda.calendars.every((c) => !c.events.length) && (
                  <p className="muted">
                    No events in this period on your selected calendars.
                  </p>
                )}
            </div>
          )}
        </>
      )}
      {error && (
        <p className="warning" role="alert">
          {error}
        </p>
      )}
    </section>
  )
}
