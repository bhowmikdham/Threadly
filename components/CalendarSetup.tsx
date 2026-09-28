import { useState } from "react"

import { api, bridge, errorText } from "../lib/api"
import {
  calendarReadReady,
  type PreferencesState
} from "../lib/scheduling-readiness"
import type { Capability } from "../lib/types"
import { CalendarAgenda } from "./CalendarAgenda"
import { Icon } from "./Icon"

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
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState(""),
    [error, setError] = useState(""),
    [calendars, setCalendars] = useState<Calendar[] | null>(null),
    [version, setVersion] = useState(0),
    [prefs, setPrefs] = useState<Preferences>(defaults)
  const connected = calendarReadReady(capabilities)
  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setMessage("")
    setError("")
    try {
      await fn()
    } catch (cause) {
      setError(errorText(cause))
    } finally {
      setBusy(false)
    }
  }
  const load = async () => {
    const list = await api<{ calendars: Calendar[] }>("/calendar/calendars")
    setCalendars(list.calendars)
    try {
      const saved = await api<{ version: number; preferences: Preferences }>(
        "/calendar/preferences"
      )
      setPrefs(saved.preferences)
      setVersion(saved.version)
      onPreferences(saved)
    } catch (cause) {
      if (cause?.code !== "calendar_preferences_missing") throw cause
      setPrefs(defaults())
      setVersion(0)
      onPreferences(null)
    }
  }
  const save = async () => {
    if (!prefs.calendar_ids.length)
      throw new Error("Select at least one calendar to check.")
    if (!prefs.working_periods.length)
      throw new Error("Choose at least one working day.")
    if (
      prefs.working_periods.some(
        (period) => period.end_minute <= period.start_minute
      )
    )
      throw new Error("Working hours must end after they start.")
    try {
      new Intl.DateTimeFormat("en", { timeZone: prefs.timezone })
    } catch {
      throw new Error("Enter a valid timezone such as Australia/Melbourne.")
    }
    const saved = await api<{ version: number; preferences: Preferences }>(
      "/calendar/preferences",
      { expected_version: version, preferences: prefs },
      "PUT"
    )
    setPrefs(saved.preferences)
    setVersion(saved.version)
    onPreferences(saved)
    setMessage(
      "Scheduling preferences saved. You can now ask for available times."
    )
  }
  return (
    <section className="calendar-setup" aria-label="Calendar setup">
      <div className="calendar-setup-heading">
        <span className="calendar-setup-icon">
          <Icon name="calendar" size={19} />
        </span>
        <div>
          <p className="setup-kicker">
            Calendar · {connected ? "Step 2 of 2" : "Step 1 of 2"}
          </p>
          <h3>
            {connected
              ? "Choose when you're available"
              : "Connect your calendar"}
          </h3>
        </div>
      </div>
      <p className="muted">
        {connected
          ? "Choose which calendars to check, then review your timezone and working hours. Nothing is booked automatically."
          : "Allow Calendar read access to check availability. Email and Calendar write permissions are separate."}
      </p>
      {!connected ? (
        <button
          className="primary"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              await bridge({ type: "LOGIN", capabilities: ["calendar_read"] })
              await onAuth()
              await load()
              setMessage(
                "Calendar connected. Review and save your preferences."
              )
            })
          }>
          {busy ? "Connecting…" : "Connect Calendar"}
        </button>
      ) : (
        <>
          <p className="connection-state" role="status">
            <Icon name="check" size={15} /> Calendar connected
            {preferencesState === "ready"
              ? " · Preferences saved"
              : " · Setup to finish"}
          </p>
          <button disabled={busy} onClick={() => void run(load)}>
            {calendars
              ? "Reload calendars and preferences"
              : "Load calendars and preferences"}
          </button>
          {calendars && calendars.length === 0 && (
            <p className="muted">
              No calendars are available to check. Reconnect Calendar or check
              access in Google.
            </p>
          )}
          {calendars && calendars.length > 0 && (
            <form
              className="calendar-preferences"
              onSubmit={(event) => {
                event.preventDefault()
                void run(save)
              }}>
              <h4>1. Calendars to check</h4>
              <p className="muted">
                Select the calendars that should count as busy when finding a
                time.
              </p>
              {calendars.map((calendar) => (
                <label className="check" key={calendar.id}>
                  <input
                    type="checkbox"
                    disabled={!calendar.can_read_busy || busy}
                    checked={prefs.calendar_ids.includes(calendar.id)}
                    onChange={(event) =>
                      setPrefs({
                        ...prefs,
                        calendar_ids: event.target.checked
                          ? [...prefs.calendar_ids, calendar.id]
                          : prefs.calendar_ids.filter(
                              (id) => id !== calendar.id
                            )
                      })
                    }
                  />
                  {calendar.summary}
                  {!calendar.can_read_busy ? " · Busy times unavailable" : ""}
                </label>
              ))}
              <h4>2. Timezone and working hours</h4>
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
              <p className="muted">
                Review these suggested hours; change them to match your week.
              </p>
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
                                    working_periods: prefs.working_periods.map(
                                      (item) =>
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
              <details className="calendar-advanced">
                <summary>Meeting length, buffers and notice</summary>
                {(
                  [
                    ["default_duration_minutes", "Meeting duration", 5, 480],
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
              <button
                className="primary"
                disabled={busy || !prefs.calendar_ids.length}>
                Save scheduling preferences
              </button>
            </form>
          )}
        </>
      )}
      {connected && (
        <CalendarAgenda
          capabilities={capabilities}
          preferencesState={preferencesState}
          onAuth={onAuth}
        />
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
