import { useEffect, useRef, useState } from "react"

import { api, bridge, errorText } from "../lib/api"
import {
  calendarReadReady,
  type PreferencesState
} from "../lib/scheduling-readiness"
import type { Capability } from "../lib/types"
import { CalendarAgenda } from "./CalendarAgenda"

type WorkingPeriod = {
  weekday: number
  start_minute: number
  end_minute: number
}
type Preferences = {
  timezone: string
  calendar_ids: string[]
  working_periods: WorkingPeriod[]
  buffer_before_minutes: number
  buffer_after_minutes: number
  minimum_notice_minutes: number
  default_duration_minutes: number
}
type Calendar = {
  id: string
  summary: string
  can_read_busy: boolean
}
const DAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday"
]
const defaults = (): Preferences => ({
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  calendar_ids: [],
  working_periods: [0, 1, 2, 3, 4].map((weekday) => ({
    weekday,
    start_minute: 540,
    end_minute: 1020
  })),
  buffer_before_minutes: 0,
  buffer_after_minutes: 0,
  minimum_notice_minutes: 60,
  default_duration_minutes: 30
})
const timeValue = (minute: number) =>
  `${String(Math.floor(minute / 60) % 24).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`

type Saved = {
  version: number
  account_version?: number
  needs_review?: boolean
  preferences: Preferences
}
type Health = {
  coverage: "complete" | "unknown"
  checked_at: string
  calendars: { calendar_id: string; status: string }[]
}

export function CalendarSetup({
  capabilities,
  preferencesState,
  onAuth,
  onPreferences
}: {
  capabilities: Capability[]
  preferencesState: PreferencesState
  onAuth: () => Promise<void>
  onPreferences: (value: any | null) => void
}) {
  const connected = calendarReadReady(capabilities)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")
  const [reloadNeeded, setReloadNeeded] = useState(false)
  const [calendars, setCalendars] = useState<Calendar[] | null>(null)
  const [saved, setSaved] = useState<Saved | null>(null)
  const [prefs, setPrefs] = useState<Preferences>(defaults)
  const [health, setHealth] = useState<Health | null>(null)
  const [review, setReview] = useState(false)
  const needsReview = review || preferencesState === "stale"
  const callbacks = useRef({ onPreferences })
  callbacks.current = { onPreferences }
  const mounted = useRef(false)
  const dirty =
    !saved || JSON.stringify(prefs) !== JSON.stringify(saved.preferences)
  const check = async (value: Saved, active = () => mounted.current) => {
    if (value.needs_review) return
    const start = new Date()
    const result = await api<Health>("/calendar/freebusy", {
      expected_preferences_version: value.version,
      start: start.toISOString(),
      end: new Date(start.getTime() + 86400000).toISOString()
    })
    if (active()) setHealth(result)
  }
  const reportError = (cause: any) => {
    if (!mounted.current) return
    if (cause?.code === "calendar_context_changed") {
      setReloadNeeded(true)
      setError(
        "Calendar settings changed elsewhere. Reload to review the latest settings before saving."
      )
    } else if (cause?.code === "calendar_preferences_stale") {
      setReview(true)
      setError("")
    } else setError(errorText(cause))
  }
  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setMessage("")
    setError("")
    try {
      await fn()
    } catch (cause) {
      reportError(cause)
    } finally {
      if (mounted.current) setBusy(false)
    }
  }
  const load = async (active = () => mounted.current) => {
    // Apply one coherent load, and ignore results from a closed or replaced screen.
    const [list, value] = await Promise.all([
      api<{ account_version?: number; calendars: Calendar[] }>(
        "/calendar/calendars"
      ),
      api<Saved>("/calendar/preferences").catch((cause) => {
        if (cause?.code === "calendar_preferences_missing") return null
        throw cause
      })
    ])
    if (!active()) return
    if (
      value &&
      value.account_version !== undefined &&
      list.account_version !== undefined &&
      value.account_version !== list.account_version
    )
      value.needs_review = true
    setCalendars(list.calendars)
    setSaved(value)
    setPrefs(value?.preferences || defaults())
    setReview(Boolean(value?.needs_review))
    setReloadNeeded(false)
    setHealth(null)
    callbacks.current.onPreferences(value)
    if (value) await check(value, active)
  }
  useEffect(() => {
    mounted.current = true
    let active = true
    if (connected) {
      setBusy(true)
      void load(() => active)
        .catch((cause) => {
          if (active) reportError(cause)
        })
        .finally(() => {
          if (active) setBusy(false)
        })
    }
    return () => {
      active = false
      mounted.current = false
    }
  }, [connected])
  const save = async () => {
    if (!prefs.calendar_ids.length)
      throw new Error("Select at least one calendar to check.")
    if (!prefs.working_periods.length)
      throw new Error("Choose at least one working day.")
    if (prefs.working_periods.some((p) => p.end_minute <= p.start_minute))
      throw new Error("Working hours must end after they start.")
    try {
      new Intl.DateTimeFormat("en", { timeZone: prefs.timezone })
    } catch {
      throw new Error("Enter a valid timezone such as Australia/Melbourne.")
    }
    const value = await api<Saved>(
      "/calendar/preferences",
      { expected_version: saved?.version || 0, preferences: prefs },
      "PUT"
    )
    if (!mounted.current) return
    setSaved(value)
    setPrefs(value.preferences)
    setReview(false)
    setHealth(null)
    callbacks.current.onPreferences(value)
    setMessage("Calendar settings saved.")
    await check(value)
  }
  // Keep disappeared selections visible and removable. Never silently omit one.
  const choices = calendars && [
    ...calendars,
    ...prefs.calendar_ids
      .filter((id) => !calendars.some((c) => c.id === id))
      .map((id) => ({
        id,
        summary: "Previously selected calendar",
        can_read_busy: false
      }))
  ]
  const failed = new Set(
    health?.calendars
      .filter((c) => c.status !== "known")
      .map((c) => c.calendar_id)
  )
  return (
    <section
      className="calendar-setup"
      aria-label="Calendar setup"
      aria-busy={busy}>
      {!connected ? (
        <>
          <h3>Connect your calendar</h3>
          <p className="muted">
            Check your availability and find a time to meet.
          </p>
          <button
            className="primary"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await bridge({ type: "LOGIN", capabilities: ["calendar_read"] })
                await onAuth()
              })
            }>
            {busy ? "Connecting…" : "Connect Calendar"}
          </button>
        </>
      ) : (
        <>
          {!calendars && (
            <p role="status" className="muted">
              {busy
                ? "Loading your calendars…"
                : "Couldn't load your calendars."}
            </p>
          )}
          {needsReview && (
            <p className="calendar-notice" role="status">
              Your connection changed. Review your calendars and save to
              continue.
            </p>
          )}
          {choices && (
            <form
              className="calendar-preferences"
              onSubmit={(event) => {
                event.preventDefault()
                void run(save)
              }}>
              <fieldset disabled={busy}>
                <legend>Calendars to check</legend>
                <p className="muted">
                  Choose which calendars count towards your availability.
                </p>
                {choices.length === 0 && (
                  <p className="muted">
                    No calendars found. Check your access at Google.
                  </p>
                )}
                <div className="calendar-choices">
                  {choices.map((calendar) => {
                    const selected = prefs.calendar_ids.includes(calendar.id)
                    const unavailable =
                      !calendar.can_read_busy || failed.has(calendar.id)
                    return (
                      <label className="calendar-choice" key={calendar.id}>
                        <input
                          type="checkbox"
                          aria-label={calendar.summary}
                          disabled={!calendar.can_read_busy && !selected}
                          checked={selected}
                          onChange={(event) => {
                            setMessage("")
                            setPrefs({
                              ...prefs,
                              calendar_ids: event.target.checked
                                ? [...prefs.calendar_ids, calendar.id]
                                : prefs.calendar_ids.filter(
                                    (id) => id !== calendar.id
                                  )
                            })
                          }}
                        />
                        <span>
                          <span>{calendar.summary}</span>
                          {unavailable && (
                            <small>
                              {calendar.can_read_busy
                                ? "Busy times unavailable"
                                : "Access unavailable. Deselect to remove."}
                            </small>
                          )}
                        </span>
                      </label>
                    )
                  })}
                </div>
                {health && !dirty && !needsReview && (
                  <div className="calendar-health" role="status">
                    <span>
                      {health.coverage === "complete"
                        ? "All selected calendars checked"
                        : "Some calendars couldn't be checked"}
                    </span>
                    <small>
                      {health.coverage === "complete"
                        ? "Availability check for the next 24 hours."
                        : "Retry, or deselect calendars that shouldn't count as busy."}
                    </small>
                  </div>
                )}
                <details
                  className="connection-disclosure working-hours"
                  open={saved ? undefined : true}>
                  <summary>
                    Working hours{" "}
                    <span>{prefs.timezone.replaceAll("_", " ")}</span>
                  </summary>
                  <label>
                    Timezone
                    <input
                      required
                      value={prefs.timezone}
                      onChange={(event) =>
                        setPrefs({ ...prefs, timezone: event.target.value })
                      }
                    />
                  </label>
                  <p className="muted">Used when suggesting meeting times.</p>
                  {DAYS.map((day, weekday) => {
                    const periods = prefs.working_periods.filter(
                      (period) => period.weekday === weekday
                    )
                    return (
                      <div className="working-day" key={day}>
                        <label className="check">
                          <input
                            type="checkbox"
                            checked={periods.length > 0}
                            onChange={(event) =>
                              setPrefs({
                                ...prefs,
                                working_periods: event.target.checked
                                  ? [
                                      ...prefs.working_periods,
                                      {
                                        weekday,
                                        start_minute: 540,
                                        end_minute: 1020
                                      }
                                    ]
                                  : prefs.working_periods.filter(
                                      (period) => period.weekday !== weekday
                                    )
                              })
                            }
                          />
                          {day}
                        </label>
                        {periods.map((period, index) => (
                          <div className="working-times" key={index}>
                            {(["start_minute", "end_minute"] as const).map(
                              (field) => (
                                <label key={field}>
                                  {field === "start_minute" ? "Start" : "End"}
                                  <input
                                    aria-label={`${day} ${field === "start_minute" ? "start" : "end"} time`}
                                    type="time"
                                    value={timeValue(period[field])}
                                    onChange={(event) => {
                                      const [hour, minute] = event.target.value
                                        .split(":")
                                        .map(Number)
                                      setPrefs({
                                        ...prefs,
                                        working_periods:
                                          prefs.working_periods.map((item) =>
                                            item === period
                                              ? {
                                                  ...period,
                                                  [field]:
                                                    field === "end_minute" &&
                                                    hour === 0 &&
                                                    minute === 0
                                                      ? 1440
                                                      : hour * 60 + minute
                                                }
                                              : item
                                          )
                                      })
                                    }}
                                  />
                                </label>
                              )
                            )}
                          </div>
                        ))}
                      </div>
                    )
                  })}
                  <details className="connection-disclosure">
                    <summary>Meeting preferences</summary>
                    {(
                      [
                        [
                          "default_duration_minutes",
                          "Meeting duration",
                          5,
                          480
                        ],
                        ["buffer_before_minutes", "Buffer before", 0, 240],
                        ["buffer_after_minutes", "Buffer after", 0, 240],
                        ["minimum_notice_minutes", "Minimum notice", 0, 10080]
                      ] as const
                    ).map(([field, label, min, max]) => (
                      <label key={field}>
                        {label} (minutes)
                        <input
                          type="number"
                          min={min}
                          max={max}
                          required
                          value={prefs[field]}
                          onChange={(event) =>
                            setPrefs({
                              ...prefs,
                              [field]: Number(event.target.value)
                            })
                          }
                        />
                      </label>
                    ))}
                  </details>
                </details>
                <div className="calendar-save-row">
                  {dirty || needsReview ? (
                    <button
                      className="primary"
                      disabled={
                        busy || reloadNeeded || !prefs.calendar_ids.length
                      }>
                      {busy ? "Checking…" : "Save changes"}
                    </button>
                  ) : (
                    <span className="muted">
                      {busy ? "Checking…" : "Settings saved"}
                    </span>
                  )}
                  {saved && !dirty && !needsReview && !reloadNeeded && (
                    <button
                      type="button"
                      className="quiet-button"
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          setHealth(null)
                          await check(saved)
                        })
                      }>
                      Check again
                    </button>
                  )}
                </div>
              </fieldset>
            </form>
          )}
          {(!calendars || reloadNeeded) && !busy && (
            <button onClick={() => void run(() => load())}>
              Reload calendars
            </button>
          )}
          <details className="connection-disclosure agenda-disclosure">
            <summary>Upcoming events</summary>
            <CalendarAgenda
              key={saved?.version || 0}
              capabilities={capabilities}
              preferencesState={
                dirty || needsReview || reloadNeeded
                  ? "missing"
                  : preferencesState
              }
              onAuth={onAuth}
            />
          </details>
        </>
      )}
      {message && (
        <p role="status" className="setup-message">
          {message}
        </p>
      )}
      {error && (
        <p role="alert" className="warning">
          {error}
        </p>
      )}
    </section>
  )
}
