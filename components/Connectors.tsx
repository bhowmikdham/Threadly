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
const calendar = ["calendar_read", "calendar_list"]
const skills: Record<ConnectorId, Feature[]> = {
  gmail: ["Search mail", "Summarise threads", "Draft replies"].map((name) => ({
    name,
    requires: ["gmail_read"]
  })),
  calendar: [
    { name: "Find availability", requires: calendar, preferences: true },
    {
      name: "Review agenda",
      requires: [...calendar, "calendar_events_read"],
      preferences: true
    },
    { name: "Plan meetings", requires: calendar, preferences: true },
    {
      name: "Create events",
      requires: [...calendar, "calendar_write"],
      preferences: true
    }
  ]
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
        ["disabled", "not_implemented"].includes(c.status)
    )
  )
    return "Not available"
  if (!required.every((c) => c?.ready)) return "Permission needed"
  if (feature.preferences && preferences !== "ready")
    return "Review calendar settings"
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
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState("")
  const [revision, setRevision] = useState(0)
  const ready = connectorReady(id, capabilities)
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
      setRevision((value) => value + 1)
      setMessage("Connection refreshed.")
    })
  return (
    <section
      className="connector-detail"
      aria-label={`${connectorNames[id]} connector`}>
      <button className="connector-back" onClick={onBack}>
        ‹ Connections
      </button>
      <div className="connector-detail-heading">
        <span className="connector-icon">
          {id === "gmail" ? (
            <GmailIcon size={24} />
          ) : (
            <GoogleCalendarIcon size={24} />
          )}
        </span>
        <div>
          <h2>{connectorNames[id]}</h2>
          <p className="muted">{user.email}</p>
        </div>
      </div>
      <p className={`connector-status${ready ? " is-on" : ""}`}>
        {ready ? "Connected" : "Not connected"}
      </p>
      <ul className="capability-chips" aria-label="Skills">
        {skills[id].map((feature) => {
          const status = featureStatus(feature, capabilities, preferencesState)
          return (
            <li
              key={feature.name}
              className={status === "Available" ? "available" : "unavailable"}
              title={status}>
              <span>{feature.name}</span>
              {status !== "Available" && (
                <span className="skill-status" aria-label={status}>
                  ·
                </span>
              )}
            </li>
          )
        })}
      </ul>
      {id === "calendar" ? (
        <CalendarSetup
          key={revision}
          capabilities={capabilities}
          preferencesState={preferencesState}
          onAuth={onAuth}
          onPreferences={onPreferences}
        />
      ) : (
        <p className="connector-description">
          Find messages, understand a thread, and prepare a reply.
        </p>
      )}
      {id === "calendar" &&
        capabilities.some(
          (c) => c.id === "calendar_write" && c.enabled && !c.ready
        ) && (
          <div className="calendar-write-setup">
            <p>
              Create events from this chat, with approval before each event by
              default.
            </p>
            <button
              disabled={busy}
              onClick={() => void reconnect("calendar_write")}>
              Enable event creation
            </button>
          </div>
        )}
      <details className="connection-disclosure">
        <summary>Account & permissions</summary>
        <p className="muted">
          Access granted to Threadly by this Google account.
        </p>
        <div className="connection-actions">
          <button
            disabled={busy}
            onClick={() =>
              void reconnect(id === "gmail" ? "gmail_read" : "calendar_read")
            }>
            {ready ? "Reconnect" : "Connect"} {connectorNames[id]}
          </button>
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
                id === "gmail" && c.id === "gmail_send" && c.enabled && !c.ready
            )
            .map((c) => (
              <button
                key={c.id}
                disabled={busy}
                onClick={() => void reconnect(c.id)}>
                {id === "gmail"
                  ? "Allow sending after review"
                  : "Enable event creation"}
              </button>
            ))}
        </div>
        <p className="muted">
          Email sending requires review. Calendar events ask for approval unless
          you choose Always allow in the current chat.
        </p>
        <p className="muted">
          Disconnecting signs you out of Threadly and disconnects both Gmail and
          Calendar.
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
          <a
            href="https://myaccount.google.com/connections"
            target="_blank"
            rel="noreferrer">
            Manage access at Google ↗
          </a>
        </p>
      </details>
      <p className="connection-privacy">
        <a href="https://threadly.au/privacy/" target="_blank" rel="noreferrer">
          Privacy
        </a>
      </p>
      {message && (
        <p role="status" className="setup-message">
          {message}
        </p>
      )}
    </section>
  )
}
