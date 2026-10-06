import { useEffect, useState } from "react"

import type { CalendarAvailability } from "../lib/calendar-availability"
import { GoogleCalendarIcon } from "./Icon"

export function CalendarAvailabilityCard({
  value,
  enabled,
  onRecheck
}: {
  value: CalendarAvailability
  enabled: boolean
  onRecheck: () => void
}) {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    setNow(Date.now())
    const delay = Date.parse(value.expires_at) - Date.now()
    if (delay <= 0) return
    const timer = setTimeout(
      () => setNow(Date.now()),
      Math.min(delay + 10, 2_147_483_647)
    )
    return () => clearTimeout(timer)
  }, [value.expires_at])
  const expired = Date.parse(value.expires_at) <= now
  const format = (instant: string, date = false) =>
    new Intl.DateTimeFormat(undefined, {
      timeZone: value.timezone,
      ...(date
        ? ({
            weekday: "short",
            day: "numeric",
            month: "short",
            year: "numeric"
          } as const)
        : ({ hour: "numeric", minute: "2-digit" } as const))
    }).format(new Date(instant))
  const result = {
    free: "Free",
    busy: "Busy",
    unknown: "Availability uncertain"
  }[value.availability]
  return (
    <section
      className="calendar-event-card calendar-availability-card"
      aria-label="Calendar availability">
      <div className="calendar-event-heading">
        <GoogleCalendarIcon size={18} />
        <span>
          {expired ? "Past availability check" : "Availability checked"}
        </span>
      </div>
      <h3>{expired ? `Previously: ${result.toLowerCase()}` : result}</h3>
      <p>
        {format(value.start, true)}
        <br />
        {format(value.start)} – {format(value.end)}
      </p>
      <p className="muted">{value.timezone} · Selected calendars</p>
      <p className="muted">
        {value.duration_minutes}-minute slot ·{" "}
        {value.duration_source === "requested"
          ? "Requested duration"
          : "Saved default"}
      </p>
      {!value.complete && (
        <p className="warning">
          Some selected calendars couldn't be checked.{" "}
          {value.availability === "busy"
            ? "Busy time was found on a checked calendar."
            : "Free time isn't confirmed."}
        </p>
      )}
      {expired && (
        <p className="muted">
          This check has expired. Check again for current availability.
        </p>
      )}
      <details className="work-details">
        <summary>Check details</summary>
        <p className="muted">
          Checked {format(value.checked_at, true)} at {format(value.checked_at)}
          .
        </p>
        {value.busy_periods.length > 0 && (
          <ul aria-label="Busy intervals">
            {value.busy_periods.map((period) => (
              <li key={`${period.start}-${period.end}`}>
                {format(period.start)} – {format(period.end)}
              </li>
            ))}
          </ul>
        )}
        <p className="muted">
          Availability only; no event was created or reserved.
        </p>
      </details>
      <div className="calendar-event-actions">
        <button
          className="primary"
          disabled={!enabled}
          onClick={onRecheck}
          title={
            enabled
              ? undefined
              : "Only the latest availability check can be refreshed. Ask again for another time."
          }>
          Check again
        </button>
      </div>
    </section>
  )
}
