import { useState } from "react"

import { bridge, errorText } from "../lib/api"
import {
  calendarReadReady,
  type PreferencesState
} from "../lib/scheduling-readiness"
import type { Capability, User } from "../lib/types"
import { CalendarSetup } from "./CalendarSetup"
import { GmailIcon, GoogleCalendarIcon } from "./Icon"

export type ConnectorId = "gmail" | "calendar"
export const connectorNames = { gmail: "Gmail", calendar: "Google Calendar" }
export function connectorReady(id: ConnectorId, capabilities: Capability[]) {
  return id === "calendar"
    ? calendarReadReady(capabilities)
    : capabilities.some((c) => c.id === "gmail_read" && c.ready)
}
export function ConnectorList({
  capabilities,
  onSelect,
  onConnect,
  connecting = ""
}: {
  capabilities: Capability[]
  onSelect: (id: ConnectorId) => void
  onConnect?: (id: ConnectorId) => void
  connecting?: string
}) {
  return (
    <ul className="connector-list">
      {(["gmail", "calendar"] as const).map((id) => {
        const ready = connectorReady(id, capabilities)
        return (
          <li key={id}>
            <button
              className="connector-row"
              aria-label={`Manage ${connectorNames[id]}`}
              onClick={() => onSelect(id)}>
              <span className="connector-icon">
                {id === "gmail" ? (
                  <GmailIcon size={19} />
                ) : (
                  <GoogleCalendarIcon size={19} />
                )}
              </span>
              <span className="connector-name">{connectorNames[id]}</span>
              <span className={`connector-status${ready ? " is-on" : ""}`}>
                {ready ? "Connected" : "Not connected"}
              </span>
              <span aria-hidden="true">›</span>
            </button>
            {!ready && onConnect && (
              <button
                className="connector-connect"
                disabled={!!connecting}
                aria-label={`Connect ${connectorNames[id]}`}
                onClick={() => onConnect(id)}>
                {connecting === id ? "Connecting…" : "Connect"}
              </button>
            )}
          </li>
        )
      })}
    </ul>
  )
}

type Feature = { name: string; requires: string[]; preferences?: boolean }
const mail = ["gmail_read"],
  calendar = ["calendar_read", "calendar_list"],
  events = [...calendar, "calendar_events_read"]
const catalog: Record<
  ConnectorId,
  {
    description: string
    skills: Feature[]
    tools: Feature[]
    sources: Feature[]
  }
> = {
  gmail: {
    description:
      "Find messages, understand a thread and prepare a reply beside your inbox.",
    skills: ["Search your mail", "Summarise a thread", "Draft a reply"].map(
      (name) => ({ name, requires: mail })
    ),
    tools: [
      { name: "Search emails", requires: mail },
      { name: "Read email threads", requires: mail },
      { name: "Send an approved email", requires: ["gmail_send"] }
    ],
    sources: ["Emails", "Email threads"].map((name) => ({
      name,
      requires: mail
    }))
  },
  calendar: {
    description:
      "Check your schedule and find times that fit your selected calendars and working hours.",
    skills: [
      { name: "Find availability", requires: calendar, preferences: true },
      { name: "Review your agenda", requires: events, preferences: true },
      {
        name: "Prepare a meeting proposal",
        requires: calendar,
        preferences: true
      }
    ],
    tools: [
      { name: "List calendars", requires: ["calendar_list"] },
      { name: "Check busy times", requires: calendar, preferences: true },
      { name: "Find free times", requires: calendar, preferences: true },
      { name: "Search events", requires: events, preferences: true },
      {
        name: "Read a day or week of events",
        requires: events,
        preferences: true
      },
      { name: "Create an approved event", requires: ["calendar_write"] }
    ],
    sources: [
      { name: "Calendars", requires: ["calendar_list"] },
      { name: "Busy times", requires: calendar },
      { name: "Events", requires: events }
    ]
  }
}
export function featureStatus(
  feature: Feature,
  capabilities: Capability[],
  preferences: PreferencesState
) {
  const required = feature.requires.map((id) =>
    capabilities.find((c) => c.id === id)
  )
  if (
    required.some(
      (c) =>
        !c ||
        c.enabled === false ||
        c.status === "disabled" ||
        c.status === "not_implemented"
    )
  )
    return "Not available"
  if (!required.every((c) => c?.ready)) return "Permission needed"
  if (feature.preferences && preferences !== "ready")
    return "Finish calendar setup"
  return "Available"
}
export function ConnectorDetails({
  id,
  user,
  capabilities,
  preferencesState,
  onAuth,
  onPreferences,
  onBack
}: {
  id: ConnectorId
  user: User
  capabilities: Capability[]
  preferencesState: PreferencesState
  onAuth: () => Promise<void>
  onPreferences: (value: any | null) => void
  onBack: () => void
}) {
  const [tab, setTab] = useState<"capabilities" | "manage">("capabilities")
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState("")
  const info = catalog[id],
    ready = connectorReady(id, capabilities)
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
  const reconnect = (capability: string) =>
    run(async () => {
      await bridge({ type: "LOGIN", capabilities: [capability] })
      await onAuth()
      setMessage("Connection refreshed.")
    })
  const group = (title: string, features: Feature[]) => (
    <section className="connector-section">
      <h3>{title}</h3>
      <ul className="capability-chips">
        {features.map((feature) => {
          const status = featureStatus(feature, capabilities, preferencesState)
          return (
            <li
              key={feature.name}
              className={status === "Available" ? "available" : "unavailable"}>
              <span>{feature.name}</span>
              <small>{status}</small>
            </li>
          )
        })}
      </ul>
    </section>
  )
  return (
    <section
      className="connector-detail"
      aria-label={`${connectorNames[id]} connector`}>
      <button className="connector-back" onClick={onBack}>
        ‹ All connectors
      </button>
      <div className="connector-detail-heading">
        <span className="connector-icon">
          {id === "gmail" ? (
            <GmailIcon size={27} />
          ) : (
            <GoogleCalendarIcon size={27} />
          )}
        </span>
        <div>
          <h2>{connectorNames[id]}</h2>
          <p className="muted">{user.email}</p>
        </div>
      </div>
      <p>{info.description}</p>
      <p className={`connector-status${ready ? " is-on" : ""}`}>
        {ready ? "Connected" : "Not connected"}
      </p>
      <div
        className="connector-tabs"
        role="tablist"
        aria-label="Connector details">
        {(["capabilities", "manage"] as const).map((value) => (
          <button
            key={value}
            role="tab"
            id={`connector-tab-${value}`}
            aria-controls={`connector-panel-${value}`}
            aria-selected={tab === value}
            tabIndex={tab === value ? 0 : -1}
            onClick={() => setTab(value)}
            onKeyDown={(event) => {
              if (
                !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
              )
                return
              event.preventDefault()
              const next =
                event.key === "Home"
                  ? "capabilities"
                  : event.key === "End"
                    ? "manage"
                    : value === "manage"
                      ? "capabilities"
                      : "manage"
              setTab(next)
              document.getElementById(`connector-tab-${next}`)?.focus()
            }}>
            {value === "capabilities" ? "Capabilities" : "Manage connection"}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`connector-panel-${tab}`}
        aria-labelledby={`connector-tab-${tab}`}>
        {tab === "capabilities" ? (
          <>
            {group("Skills", info.skills)}
            {group("Tools", info.tools)}
            {group("Data sources", info.sources)}
            {id === "calendar" && (
              <p className="muted">
                Event editing, deletion, RSVP, shared free-time searches and
                room booking are not available yet.
              </p>
            )}
            <section className="connector-section">
              <h3>Privacy and control</h3>
              <p>
                Threadly uses the Google access you grant to answer your
                requests. Sending mail and creating events require a separate
                permission and your review.
              </p>
              <p className="muted">
                Status reflects stored permissions, not a live Google connection
                test.
              </p>
              <a
                href="https://threadly.au/privacy/"
                target="_blank"
                rel="noreferrer">
                Read the privacy information
              </a>
            </section>
          </>
        ) : (
          <>
            <section className="connector-section">
              <h3>Connection</h3>
              <div className="button-row">
                <button
                  disabled={busy}
                  onClick={() =>
                    void reconnect(
                      id === "gmail" ? "gmail_read" : "calendar_read"
                    )
                  }>
                  {ready ? "Reconnect" : "Connect"} {connectorNames[id]}
                </button>
                <button disabled={busy} onClick={() => void run(onAuth)}>
                  Refresh status
                </button>
              </div>
              {id === "calendar" &&
                !capabilities.some(
                  (c) => c.id === "calendar_events_read" && c.ready
                ) && (
                  <button
                    disabled={busy}
                    onClick={() => void reconnect("calendar_events_read")}>
                    Allow event details
                  </button>
                )}
              {capabilities
                .filter(
                  (c) =>
                    c.id ===
                      (id === "gmail" ? "gmail_send" : "calendar_write") &&
                    c.enabled &&
                    !c.ready
                )
                .map((c) => (
                  <button
                    key={c.id}
                    disabled={busy}
                    onClick={() => void reconnect(c.id)}>
                    {id === "gmail"
                      ? "Allow sending after review"
                      : "Allow booking after review"}
                  </button>
                ))}
            </section>
            {id === "calendar" && (
              <CalendarSetup
                capabilities={capabilities}
                preferencesState={preferencesState}
                onAuth={onAuth}
                onPreferences={onPreferences}
              />
            )}
            <section className="connector-section">
              <h3>Google account access</h3>
              <p>
                Gmail and Calendar share this Google account. Disconnecting
                stops both connectors in Threadly and signs you out.
              </p>
              <button
                className="connector-disconnect"
                disabled={busy}
                onClick={() => {
                  if (
                    window.confirm(
                      "Disconnect your Google account from Threadly? This stops both Gmail and Calendar access and signs you out."
                    )
                  )
                    void run(async () => {
                      await bridge({ type: "DISCONNECT_GOOGLE" })
                      await onAuth()
                    })
                }}>
                Disconnect Google account
              </button>
              <p className="muted">
                To remove Google's permission grant too, use{" "}
                <a
                  href="https://myaccount.google.com/connections"
                  target="_blank"
                  rel="noreferrer">
                  Google account connections
                </a>
                .
              </p>
            </section>
          </>
        )}
        {message && <p role="status">{message}</p>}
      </div>
    </section>
  )
}
