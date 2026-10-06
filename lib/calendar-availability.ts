export type CalendarAvailability = {
  operation: "check_time_availability"
  availability: "free" | "busy" | "unknown"
  scope: "selected_calendars"
  start: string
  end: string
  timezone: string
  duration_minutes: number
  duration_source: "saved_default" | "requested"
  complete: boolean
  checked_at: string
  expires_at: string
  busy_periods: { start: string; end: string }[]
}

// Older servers/history have no card payload. Never reconstruct availability from prose.
export function calendarAvailability(
  value: any
): CalendarAvailability | undefined {
  if (
    !value ||
    value.operation !== "check_time_availability" ||
    value.scope !== "selected_calendars" ||
    !["free", "busy", "unknown"].includes(value.availability) ||
    !["saved_default", "requested"].includes(value.duration_source) ||
    !Number.isInteger(value.duration_minutes) ||
    value.duration_minutes < 5 ||
    value.duration_minutes > 480 ||
    typeof value.complete !== "boolean"
  )
    return
  for (const key of ["start", "end", "checked_at", "expires_at"])
    if (
      typeof value[key] !== "string" ||
      !Number.isFinite(Date.parse(value[key]))
    )
      return
  if (
    Date.parse(value.end) <= Date.parse(value.start) ||
    Date.parse(value.expires_at) <= Date.parse(value.checked_at) ||
    typeof value.timezone !== "string"
  )
    return
  try {
    new Intl.DateTimeFormat(undefined, { timeZone: value.timezone })
  } catch {
    return
  }
  if (!Array.isArray(value.busy_periods) || value.busy_periods.length > 10)
    return
  const periods: CalendarAvailability["busy_periods"] = []
  for (const period of value.busy_periods) {
    if (
      typeof period?.start !== "string" ||
      typeof period?.end !== "string" ||
      !Number.isFinite(Date.parse(period.start)) ||
      !Number.isFinite(Date.parse(period.end)) ||
      Date.parse(period.start) < Date.parse(value.start) ||
      Date.parse(period.end) > Date.parse(value.end) ||
      Date.parse(period.start) >= Date.parse(period.end)
    )
      return
    periods.push({ start: period.start, end: period.end })
  }
  const complete = value.complete && value.coverage === "complete"
  const availability = periods.length
    ? "busy"
    : value.availability === "free" && !complete
      ? "unknown"
      : value.availability
  return {
    operation: value.operation,
    availability,
    scope: value.scope,
    start: value.start,
    end: value.end,
    timezone: value.timezone,
    duration_minutes: value.duration_minutes,
    duration_source: value.duration_source,
    complete,
    checked_at: value.checked_at,
    expires_at: value.expires_at,
    busy_periods: periods
  }
}
