import { useEffect, useState } from "react"

import { api, bridge, errorText } from "../lib/api"
import { backendOrigin } from "../lib/security"
import type { Capability, User } from "../lib/types"

export function Settings({
  user,
  capabilities,
  onAuth,
  onClose,
  onPreferences
}: {
  user: User | null
  capabilities: Capability[]
  onAuth: () => Promise<void>
  onClose: () => void
  onPreferences: (x: any) => void
}) {
  const [origin, setOrigin] = useState(""),
    [redirect, setRedirect] = useState(""),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState(""),
    [calendars, setCalendars] = useState<any[]>([]),
    [version, setVersion] = useState(0),
    [prefs, setPrefs] = useState<any>({
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
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
  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setMessage("")
    try {
      await fn()
    } catch (e) {
      setMessage(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  useEffect(() => {
    void bridge<any>({ type: "STATUS" }).then((s) => {
      setOrigin(s.origin)
      setRedirect(s.redirectUri)
    })
  }, [])
  const load = () =>
    run(async () => {
      const l = await api("/calendar/calendars")
      setCalendars(l.calendars)
      try {
        const p = await api("/calendar/preferences")
        setPrefs(p.preferences)
        setVersion(p.version)
        onPreferences(p)
      } catch (e) {
        if (e.code !== "calendar_preferences_missing") throw e
      }
    })
  return (
    <section className="settings">
      <div className="card-heading">
        <h2>Settings</h2>
        <button onClick={onClose}>Back to chat</button>
      </div>
      <label>
        Backend server
        <input value={origin} onChange={(e) => setOrigin(e.target.value)} />
      </label>
      <button
        disabled={busy}
        onClick={() => {
          try {
            const next = backendOrigin(origin)
            const permission = next.startsWith("https:")
              ? chrome.permissions.request({ origins: [next + "/*"] })
              : Promise.resolve(true)
            void permission
              .then((ok) => {
                if (!ok) {
                  setMessage("Server permission was not granted.")
                  return
                }
                void run(async () => {
                  await bridge({ type: "CONFIGURE", origin: next })
                  await onAuth()
                  setMessage("Server saved. Sign in to continue.")
                })
              })
              .catch((e) => setMessage(errorText(e)))
          } catch (e) {
            setMessage(errorText(e))
          }
        }}>
        Save server
      </button>
      <p className="muted">
        For EC2 development, keep your localhost tunnel open. Changing servers
        signs you out.
      </p>
      <details>
        <summary>Google sign-in setup</summary>
        <p>
          Add this exact redirect URI to the backend’s Google Web application
          client and server allowlist:
        </p>
        <code className="wrap">{redirect}</code>
      </details>
      {user && (
        <>
          <h3>Connections</h3>
          {capabilities.map((c) => (
            <p key={c.id}>
              {c.id.replaceAll("_", " ")}:{" "}
              <b>{c.ready ? "Ready" : c.status.replaceAll("_", " ")}</b>
            </p>
          ))}
          <div className="button-row">
            <button
              disabled={busy}
              onClick={() =>
                run(async () => {
                  await bridge({ type: "LOGIN", capabilities: ["gmail_read"] })
                  await onAuth()
                  setMessage("Gmail connection updated.")
                })
              }>
              Reconnect Gmail
            </button>
            <button
              disabled={busy}
              onClick={() =>
                run(async () => {
                  await bridge({
                    type: "LOGIN",
                    capabilities: ["calendar_read"]
                  })
                  await onAuth()
                  setMessage("Calendar connected.")
                })
              }>
              Connect Calendar
            </button>
          </div>
          <details>
            <summary>Optional sending and booking permissions</summary>
            <p>
              These require server pilot access. Connecting does not enable
              automatic sends or bookings.
            </p>
            <button
              disabled={busy}
              onClick={() =>
                run(async () => {
                  await bridge({ type: "LOGIN", capabilities: ["gmail_send"] })
                  await onAuth()
                })
              }>
              Connect sending
            </button>
            <button
              disabled={busy}
              onClick={() =>
                run(async () => {
                  await bridge({
                    type: "LOGIN",
                    capabilities: ["calendar_write"]
                  })
                  await onAuth()
                })
              }>
              Connect booking
            </button>
          </details>
          <h3>Scheduling preferences</h3>
          <button disabled={busy} onClick={load}>
            Load calendars and preferences
          </button>
          {calendars.length > 0 && (
            <form
              onSubmit={(e) => {
                e.preventDefault()
                void run(async () => {
                  const p = await api(
                    "/calendar/preferences",
                    { expected_version: version, preferences: prefs },
                    "PUT"
                  )
                  setVersion(p.version)
                  onPreferences(p)
                  setMessage("Scheduling preferences saved.")
                })
              }}>
              <label>
                Timezone
                <input
                  required
                  value={prefs.timezone}
                  onChange={(e) =>
                    setPrefs({ ...prefs, timezone: e.target.value })
                  }
                />
              </label>
              {calendars.map((c) => (
                <label className="check" key={c.id}>
                  <input
                    type="checkbox"
                    disabled={!c.can_read_busy}
                    checked={prefs.calendar_ids.includes(c.id)}
                    onChange={(e) =>
                      setPrefs({
                        ...prefs,
                        calendar_ids: e.target.checked
                          ? [...prefs.calendar_ids, c.id]
                          : prefs.calendar_ids.filter((id) => id !== c.id)
                      })
                    }
                  />
                  {c.summary}
                </label>
              ))}
              <p>Working hours (local timezone)</p>
              {[0, 1, 2, 3, 4, 5, 6].map((day) => {
                const periods = prefs.working_periods.filter(
                  (p) => p.weekday === day
                )
                return (
                  <div className="working-day" key={day}>
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={!!periods.length}
                        onChange={(e) =>
                          setPrefs({
                            ...prefs,
                            working_periods: e.target.checked
                              ? [
                                  ...prefs.working_periods,
                                  {
                                    weekday: day,
                                    start_minute: 540,
                                    end_minute: 1020
                                  }
                                ]
                              : prefs.working_periods.filter(
                                  (p) => p.weekday !== day
                                )
                          })
                        }
                      />
                      {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][day]}
                    </label>
                    {periods.map((p, index) => (
                      <div key={index} className="button-row">
                        {["start_minute", "end_minute"].map((field) => (
                          <input
                            key={field}
                            aria-label={`${day} ${field}`}
                            type="time"
                            value={`${String(Math.floor(p[field] / 60) % 24).padStart(2, "0")}:${String(p[field] % 60).padStart(2, "0")}`}
                            onChange={(e) => {
                              const [h, m] = e.target.value
                                .split(":")
                                .map(Number)
                              setPrefs({
                                ...prefs,
                                working_periods: prefs.working_periods.map(
                                  (x) =>
                                    x === p
                                      ? {
                                          ...p,
                                          [field]:
                                            field === "end_minute" &&
                                            h === 0 &&
                                            m === 0
                                              ? 1440
                                              : h * 60 + m
                                        }
                                      : x
                                )
                              })
                            }}
                          />
                        ))}
                      </div>
                    ))}
                  </div>
                )
              })}
              {[
                ["default_duration_minutes", "Meeting duration"],
                ["buffer_before_minutes", "Buffer before"],
                ["buffer_after_minutes", "Buffer after"],
                ["minimum_notice_minutes", "Minimum notice"]
              ].map(([field, label]) => (
                <label key={field}>
                  {label} (minutes)
                  <input
                    type="number"
                    min={field === "default_duration_minutes" ? 5 : 0}
                    max={
                      field === "minimum_notice_minutes"
                        ? 10080
                        : field === "default_duration_minutes"
                          ? 480
                          : 240
                    }
                    value={prefs[field]}
                    onChange={(e) =>
                      setPrefs({ ...prefs, [field]: Number(e.target.value) })
                    }
                  />
                </label>
              ))}
              <button
                className="primary"
                disabled={busy || !prefs.calendar_ids.length}>
                Save scheduling preferences
              </button>
            </form>
          )}
        </>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  )
}
