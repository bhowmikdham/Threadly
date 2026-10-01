import { showUpdateNotice } from "./lib/extension-updates"
import {
  allowedRequest,
  backendOrigin,
  base64url,
  callbackCode,
  DEFAULT_BACKEND,
  publishedBackendBuild,
  trustedSender
} from "./lib/security"

type Session = {
  jwt: string
  user: { id: number; email: string; name: string | null }
  origin: string
}
let signingIn = false
let signingOut = false
let sessionGeneration = 0
let refreshing: Promise<string> | null = null
let sessionWriteTail: Promise<void> = Promise.resolve()
function mutateSession<T>(operation: () => Promise<T>): Promise<T> {
  const result = sessionWriteTail.then(operation)
  sessionWriteTail = result.then(
    () => undefined,
    () => undefined
  )
  return result
}
const protectStorage = async () => {
  await chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" })
  await chrome.storage.session.setAccessLevel({
    accessLevel: "TRUSTED_CONTEXTS"
  })
}
void protectStorage()
chrome.runtime.onInstalled.addListener(async (details) => {
  void chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true })
  await showUpdateNotice(details, chrome.runtime.getManifest().version, {
    read: async () =>
      (await chrome.storage.local.get("lastUpdateNoticeVersion"))
        .lastUpdateNoticeVersion,
    save: async (version) =>
      chrome.storage.local.set({ lastUpdateNoticeVersion: version }),
    open: async () => {
      await chrome.tabs.create({
        url: chrome.runtime.getURL("tabs/updated.html")
      })
    }
  }).catch(() => {
    /* A notice failure must not interrupt storage migration. */
  })
  // Historical frontend cached subject-derived badges without an account binding.
  const values = await chrome.storage.local.get()
  await chrome.storage.local.remove(
    Object.keys(values).filter((k) => k.startsWith("class_"))
  )
})
async function settings() {
  if (publishedBackendBuild) return DEFAULT_BACKEND
  return backendOrigin(
    (await chrome.storage.local.get("backendOrigin")).backendOrigin ||
      DEFAULT_BACKEND
  )
}
async function session(): Promise<Session | null> {
  return (
    (await chrome.storage.session.get("threadlySession")).threadlySession ||
    null
  )
}
async function transport(
  origin: string,
  path: string,
  method: string,
  body?: unknown,
  jwt?: string,
  timeoutMs = 150000
) {
  if (!allowedRequest(path, method))
    throw new Error("Unsupported backend request.")
  const response = await fetch(origin + path, {
    method,
    headers: {
      Accept: "application/json",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(jwt ? { Authorization: `Bearer ${jwt}` } : {})
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "omit",
    cache: "no-store",
    redirect: "error",
    signal: AbortSignal.timeout(timeoutMs)
  }).catch((error: unknown) => {
    const timedOut =
      error instanceof DOMException && error.name === "TimeoutError"
    const local = ["127.0.0.1", "localhost"].includes(new URL(origin).hostname)
    const message = timedOut
      ? "The backend took too long to respond."
      : `Cannot reach the Threadly backend at ${origin}.`
    const recovery = local
      ? "Keep the EC2 connection terminal open and check that the server is running."
      : publishedBackendBuild
        ? jwt
          ? "Check your connection; if server access was removed, use Reconnect server in Settings."
          : "Check your connection."
        : "Check your connection and the server address in Settings."
    throw Object.assign(
      new Error(
        `${message} ${recovery}${jwt ? " Check task or action status before submitting again; a request may still be processing." : " Then try Sign in again."}`
      ),
      { code: timedOut ? "backend_timeout" : "backend_unreachable" }
    )
  })
  const text = await response.text()
  let data: any
  try {
    data = JSON.parse(text)
  } catch {
    throw new Error("The server returned an unreadable response.")
  }
  if (!response.ok) {
    const error: any = new Error(
      data?.error?.message || `Request failed (${response.status}).`
    )
    error.code = data?.error?.code || "backend_error"
    error.status = response.status
    throw error
  }
  return data
}
async function activeSession() {
  const generation = sessionGeneration
  const saved = await session(),
    origin = await settings()
  if (!saved || saved.origin !== origin || generation !== sessionGeneration)
    throw Object.assign(new Error("Sign in to Threadly first."), {
      code: "login_required",
      status: 401
    })
  let expiry = 0
  try {
    expiry = JSON.parse(
      atob(saved.jwt.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))
    ).exp
  } catch {
    /* Server verifies the token; malformed local state cannot bypass it. */
  }
  if (expiry * 1000 - Date.now() < 120000) {
    if (!refreshing)
      refreshing = transport(
        origin,
        "/auth/refresh",
        "POST",
        undefined,
        saved.jwt
      )
        .then((data) =>
          mutateSession(async () => {
            const current = await session()
            if (
              generation !== sessionGeneration ||
              !current ||
              current.jwt !== saved.jwt
            )
              throw new Error("Your session changed. Sign in again.")
            await chrome.storage.session.set({
              threadlySession: { ...saved, jwt: data.jwt }
            })
            return data.jwt
          })
        )
        .finally(() => {
          refreshing = null
        })
    saved.jwt = await refreshing
  }
  if (generation !== sessionGeneration)
    throw new Error("Your session changed. Sign in again.")
  return saved
}
async function login(capabilities?: string[]) {
  if (signingIn || signingOut)
    throw new Error("A sign-in or sign-out is already in progress.")
  signingIn = true
  try {
    const generation = sessionGeneration
    const origin = await settings(),
      redirect_uri = chrome.identity.getRedirectURL("oauth/callback")
    const code_verifier = base64url(crypto.getRandomValues(new Uint8Array(32)))
    const code_challenge = base64url(
      new Uint8Array(
        await crypto.subtle.digest(
          "SHA-256",
          new TextEncoder().encode(code_verifier)
        )
      )
    )
    const old = capabilities ? await activeSession() : null
    const start = await transport(
      origin,
      capabilities ? "/auth/google/reconnect" : "/auth/google/begin",
      "POST",
      {
        redirect_uri,
        code_challenge,
        ...(capabilities ? { capabilities } : {})
      },
      old?.jwt
    )
    const auth = new URL(start.authorization_url)
    if (auth.origin !== "https://accounts.google.com")
      throw new Error("Unexpected sign-in provider.")
    const callback = await chrome.identity.launchWebAuthFlow({
      url: auth.href,
      interactive: true
    })
    if (!callback) throw new Error("Sign-in was cancelled.")
    const code = callbackCode(callback, redirect_uri, start.state)
    if (origin !== (await settings()))
      throw new Error("Server changed during login. Start again.")
    const result = await transport(origin, "/auth/google/exchange", "POST", {
      code,
      redirect_uri,
      state: start.state,
      code_verifier
    })
    await mutateSession(async () => {
      if (generation !== sessionGeneration || origin !== (await settings()))
        throw new Error("Session changed during login. Start again.")
      await chrome.storage.session.set({
        threadlySession: { ...result, origin }
      })
    })
    return result.user
  } finally {
    signingIn = false
  }
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (
    message?.type === "OPEN_SIDE_PANEL" &&
    sender.id === chrome.runtime.id &&
    sender.tab?.id &&
    sender.tab.url?.startsWith("https://mail.google.com/")
  ) {
    void chrome.sidePanel.open({ tabId: sender.tab.id })
    return false
  }
  if (
    message?.channel !== "threadly" ||
    !trustedSender(sender, chrome.runtime.id)
  )
    return false
  void (async () => {
    await protectStorage()
    switch (message.type) {
      case "STATUS": {
        const s = await session()
        return {
          user: s?.origin === (await settings()) ? s.user : null,
          origin: await settings(),
          redirectUri: chrome.identity.getRedirectURL("oauth/callback")
        }
      }
      case "LOGIN":
        return login(message.capabilities)
      case "DISCONNECT_GOOGLE": {
        if (signingIn || signingOut)
          throw new Error("Finish the current sign-in or sign-out first.")
        signingOut = true
        try {
          const saved = await activeSession()
          const result = await transport(
            saved.origin,
            "/auth/google/disconnect",
            "POST",
            undefined,
            saved.jwt,
            15000
          )
          if (result?.connected !== false)
            throw new Error(
              "The server did not confirm disconnection. Refresh status before trying again."
            )
          sessionGeneration++
          await mutateSession(() =>
            chrome.storage.session.remove("threadlySession")
          )
          return result
        } finally {
          signingOut = false
        }
      }
      case "LOGOUT": {
        if (signingOut)
          throw new Error("A sign-out or disconnect is already in progress.")
        // Clear the browser token immediately, even if the server cannot be reached.
        // This endpoint revokes Threadly sessions; it does not disconnect Google.
        sessionGeneration++
        signingOut = true
        try {
          const previous = await mutateSession(async () => {
            const saved = await session()
            await chrome.storage.session.remove("threadlySession")
            return saved
          })
          if (!previous) return { serverRevoked: false }
          // Never send a token to an origin different from the configured API.
          if (previous.origin !== (await settings()))
            return { serverRevoked: false }
          const result = await transport(
            previous.origin,
            "/auth/logout",
            "POST",
            undefined,
            previous.jwt,
            10000
          )
          return { serverRevoked: result?.signed_out === true }
        } catch {
          // A local sign-out is not evidence of server-side revocation.
          return { serverRevoked: false }
        } finally {
          signingOut = false
        }
      }
      case "CONFIGURE": {
        if (publishedBackendBuild)
          throw new Error(
            "This extension is configured for the Threadly server."
          )
        if (signingIn || signingOut)
          throw new Error("Finish sign-in or sign-out before changing servers.")
        const origin = backendOrigin(message.origin)
        sessionGeneration++
        await mutateSession(async () => {
          await chrome.storage.local.set({ backendOrigin: origin })
          await chrome.storage.session.clear()
        })
        return { origin }
      }
      case "ACTION_REFERENCE": {
        const saved = await activeSession()
        if (
          !["email", "calendar"].includes(message.kind) ||
          !/^[a-zA-Z0-9_-]{1,128}$/.test(message.artifactId)
        )
          throw new Error("Invalid action reference.")
        const key = `action:${saved.origin}:${saved.user.id}:${message.kind}:${message.artifactId}`
        if (message.actionId !== undefined) {
          if (!/^[a-zA-Z0-9_-]{1,128}$/.test(message.actionId))
            throw new Error("Invalid action reference.")
          // IDs only. Never retain email text, drafts, tokens, or event details here.
          await chrome.storage.local.set({ [key]: message.actionId })
          return null
        }
        return (await chrome.storage.local.get(key))[key] || null
      }
      case "API": {
        // No direct provider calls or cookie forwarding. The server owns authorization.
        if (
          message.path.startsWith("/auth/") ||
          message.path.startsWith("/sync/")
        )
          throw new Error("Use the dedicated sign-in controls.")
        try {
          const s = await activeSession()
          return await transport(
            s.origin,
            message.path,
            message.method,
            message.body,
            s.jwt
          )
        } catch (error) {
          if (error.status === 401)
            await chrome.storage.session.remove("threadlySession")
          throw error
        }
      }
      default:
        throw new Error("Unknown extension request.")
    }
  })()
    .then((data) => respond({ ok: true, data }))
    .catch((error) =>
      respond({
        ok: false,
        error: {
          code: error.code || "connection_failed",
          message: error.message || "Connection failed.",
          status: error.status || 0
        }
      })
    )
  return true
})
