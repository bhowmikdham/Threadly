import { useEffect, useState } from "react"

import { bridge, errorText } from "../lib/api"
import { requestBackendAccess } from "../lib/backend-access"
import { type PreferencesState } from "../lib/scheduling-readiness"
import {
  backendOrigin,
  DEFAULT_BACKEND,
  publishedBackendBuild
} from "../lib/security"
import type { Capability, User } from "../lib/types"
import { CalendarSetup } from "./CalendarSetup"

export function Settings({
  user,
  capabilities,
  preferencesState = "missing",
  onAuth,
  onClose,
  onPreferences
}: {
  user: User | null
  capabilities: Capability[]
  preferencesState?: PreferencesState
  onAuth: () => Promise<void>
  onClose: () => void
  onPreferences: (value: any | null) => void
}) {
  const [origin, setOrigin] = useState(""),
    [redirect, setRedirect] = useState(""),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState("")
  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setMessage("")
    try {
      await fn()
    } catch (cause) {
      setMessage(errorText(cause))
    } finally {
      setBusy(false)
    }
  }
  useEffect(() => {
    void bridge<any>({ type: "STATUS" }).then((status) => {
      setOrigin(status.origin)
      setRedirect(status.redirectUri)
    })
  }, [])
  return (
    <section className="settings">
      <div className="card-heading">
        <h2>Settings</h2>
        <button onClick={onClose}>Back to chat</button>
      </div>
      {user && (
        <>
          {publishedBackendBuild && (
            <section className="settings-section">
              <h3>Threadly server</h3>
              <p className="muted">
                Reconnect if browser access to the Threadly server was removed.
              </p>
              <button
                disabled={busy}
                onClick={() => {
                  const access = requestBackendAccess(DEFAULT_BACKEND)
                  void run(async () => {
                    await access
                    await onAuth()
                    setMessage("Server connection ready.")
                  })
                }}>
                Reconnect server
              </button>
            </section>
          )}
          <CalendarSetup
            capabilities={capabilities}
            preferencesState={preferencesState}
            onAuth={onAuth}
            onPreferences={onPreferences}
          />
          <section className="settings-section">
            <h3>Google connection</h3>
            <p className="muted">
              Connected as {user.email}. Gmail and Calendar permissions are
              requested separately.
            </p>
            <p className="connection-state">
              Gmail:{" "}
              {capabilities.find((capability) => capability.id === "gmail_read")
                ?.ready
                ? "Ready"
                : "Reconnect needed"}
            </p>
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await bridge({ type: "LOGIN", capabilities: ["gmail_read"] })
                  await onAuth()
                  setMessage("Gmail connection updated.")
                })
              }>
              Reconnect Gmail
            </button>
            <details>
              <summary>Optional sending and booking permissions</summary>
              <p className="muted">
                These require server pilot access. Connecting does not enable
                automatic sends or bookings.
              </p>
              <div className="button-row">
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await bridge({
                        type: "LOGIN",
                        capabilities: ["gmail_send"]
                      })
                      await onAuth()
                    })
                  }>
                  Connect sending
                </button>
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await bridge({
                        type: "LOGIN",
                        capabilities: ["calendar_write"]
                      })
                      await onAuth()
                    })
                  }>
                  Connect booking
                </button>
              </div>
            </details>
          </section>
        </>
      )}
      {!publishedBackendBuild && (
        <section className="settings-section">
          <h3>Backend server</h3>
          <label>
            Server origin
            <input
              aria-label="Backend server"
              value={origin}
              onChange={(event) => setOrigin(event.target.value)}
            />
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
                  .catch((cause) => setMessage(errorText(cause)))
              } catch (cause) {
                setMessage(errorText(cause))
              }
            }}>
            Save server
          </button>
          <p className="muted">Changing servers signs you out.</p>
          <details>
            <summary>Google sign-in setup</summary>
            <p>
              Add this exact redirect URI to the backend’s Google Web
              application client and server allowlist:
            </p>
            <code className="wrap">{redirect}</code>
          </details>
        </section>
      )}
      {publishedBackendBuild && !user && (
        <p className="muted">
          Sign in to manage your Google and Calendar access.
        </p>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  )
}
