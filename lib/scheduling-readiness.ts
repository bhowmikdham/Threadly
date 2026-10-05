import type { Capability } from "./types"

export type PreferencesState =
  | "loading"
  | "missing"
  | "ready"
  | "unavailable"
  | "stale"

export function calendarReadReady(capabilities: Capability[]): boolean {
  return ["calendar_read", "calendar_list"].every(
    (id) =>
      capabilities.find((capability) => capability.id === id)?.ready === true
  )
}

/** Event titles need their own grant; free/busy scheduling does not. */
export function calendarEventsReadReady(capabilities: Capability[]): boolean {
  return (
    calendarReadReady(capabilities) &&
    capabilities.find((capability) => capability.id === "calendar_events_read")
      ?.ready === true
  )
}

export function schedulingReadiness(
  capabilities: Capability[],
  preferences: PreferencesState
) {
  if (!calendarReadReady(capabilities))
    return {
      state: "connect" as const,
      label: "Calendar not connected",
      detail:
        "Connect Calendar to check your availability and suggest meeting times.",
      action: "Connect Calendar"
    }
  if (preferences === "loading")
    return {
      state: "loading" as const,
      label: "Checking Calendar setup",
      detail: "Checking your saved scheduling preferences.",
      action: null
    }
  if (preferences === "stale")
    return {
      state: "preferences" as const,
      label: "Review your calendars",
      detail:
        "Your connection changed. Review and save your calendar settings.",
      action: "Review calendars"
    }
  if (preferences === "unavailable")
    return {
      state: "unavailable" as const,
      label: "Calendar setup needs a check",
      detail: "We couldn't confirm your saved calendars and working hours.",
      action: "Check Calendar setup"
    }
  if (preferences === "missing")
    return {
      state: "preferences" as const,
      label: "Choose when you're available",
      detail:
        "Select a calendar, timezone and working hours before scheduling.",
      action: "Set up Calendar"
    }
  return {
    state: "ready" as const,
    label: "Calendar connected",
    detail: "Your selected calendars and working hours are saved.",
    action: "Review setup"
  }
}

const SETUP_ERRORS = new Set([
  "calendar_connection_required",
  "calendar_access_denied",
  "calendar_preferences_missing",
  "calendar_preferences_required",
  "calendar_not_selectable",
  "calendar_preferences_stale",
  "calendar_context_changed",
  "calendar_coverage_incomplete"
])

export function needsCalendarSetup(code?: string | null): boolean {
  return Boolean(code && SETUP_ERRORS.has(code))
}

export function preferencesStatus(
  value: { needs_review?: boolean } | null
): PreferencesState {
  return !value ? "missing" : value.needs_review ? "stale" : "ready"
}
