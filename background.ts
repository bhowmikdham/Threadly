import { retryAfterSeconds } from "./lib/classification"
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
  refresh_token?: string
  user: { id: number; email: string; name: string | null }
  origin: string
}
let signingIn = false
let signingOut = false
let sessionGeneration = 0
let refreshing: Promise<string> | null = null
let refreshGeneration = -1
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
const storageReady = (async () => {
  await protectStorage()
  const saved = (await chrome.storage.local.get("threadlySession"))
    .threadlySession
  const legacy = (await chrome.storage.session.get("threadlySession"))
    .threadlySession
  if (!saved && legacy)
    await chrome.storage.local.set({ threadlySession: legacy })
  await chrome.storage.session.remove("threadlySession")
})()
// Every message awaits this promise; startup failure must not leak an unhandled rejection.
void storageReady.catch(() => {})
chrome.runtime.onInstalled.addListener(async (details) => {
  await storageReady
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
    (await chrome.storage.local.get("threadlySession")).threadlySession || null
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
    error.retryAfterSeconds = retryAfterSeconds(
      response.headers.get("Retry-After")
    )
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
  if (!saved.refresh_token || expiry * 1000 - Date.now() < 120000) {
    if (!refreshing || refreshGeneration !== generation) {
      refreshGeneration = generation
      const pending: Promise<string> = transport(
        origin,
        "/auth/refresh",
        "POST",
        undefined,
        saved.refresh_token || saved.jwt
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
            await chrome.storage.local.set({
              threadlySession: {
                ...saved,
                jwt: data.jwt,
                refresh_token: data.refresh_token || saved.refresh_token
              }
            })
            return data.jwt
          })
        )
        .finally(() => {
          if (refreshing === pending) refreshing = null
        })
      refreshing = pending
    }
    try {
      saved.jwt = await refreshing
    } catch (error) {
      if (error.status === 401 && !(await discardSession(saved, generation)))
        throw Object.assign(
          new Error("Your session changed. Please try again."),
          {
            code: "session_changed",
            status: 409
          }
        )
      throw error
    }
  }
  if (generation !== sessionGeneration)
    throw new Error("Your session changed. Sign in again.")
  return saved
}
async function discardSession(expected: Session, generation: number) {
  return mutateSession(async () => {
    const current = await session()
    if (generation !== sessionGeneration || current?.jwt !== expected.jwt)
      return false
    sessionGeneration++
    await chrome.storage.local.remove("threadlySession")
    await chrome.storage.session.remove("threadlySession")
    return true
  })
}

async function login(capabilities?: string[], expectedUserId?: number) {
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
    if (expectedUserId !== undefined && old?.user.id !== expectedUserId)
      throw new Error("Your account changed. Reload before connecting Google.")
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
      await chrome.storage.local.set({
        threadlySession: { ...result, origin }
      })
    })
    return result.user
  } finally {
    signingIn = false
  }
}
// Inbox badges: the server runs at most two classifications per process, so
// every Gmail tab shares these two slots.
let classifying = 0
const classifyWaiting: (() => void)[] = []
// When the server says to wait, every Gmail tab waits: the model budget is
// shared by the whole server, and Gmail limits are per account.
let modelPausedUntil = 0
const gmailPausedUntil = new Map<number, number>()
const pauseLeft = (userId: number) =>
  Math.max(modelPausedUntil, gmailPausedUntil.get(userId) || 0) - Date.now()
const cooldown = (userId: number) => ({
  ok: false,
  code: "classification_cooldown",
  retryAfterSeconds: Math.ceil(pauseLeft(userId) / 1000)
})
// Signing out, from wherever, forgets kept badge results.
chrome.storage.onChanged.addListener((changes, area) => {
  if (
    area !== "local" ||
    !changes.threadlySession ||
    changes.threadlySession.newValue
  )
    return
  void chrome.storage.session
    .get()
    .then((all) =>
      chrome.storage.session.remove(
        Object.keys(all).filter((k) => k.startsWith("classification:"))
      )
    )
})
async function keepClassification(key: string, data: any) {
  const all = await chrome.storage.session.get()
  const expired = Object.keys(all).filter(
    (k) =>
      k.startsWith("classification:") &&
      !(Date.parse(all[k]?.valid_until) > Date.now())
  )
  if (expired.length) await chrome.storage.session.remove(expired)
  await chrome.storage.session.set({ [key]: data })
}
async function classifyThread(message: any) {
  const threadId = String(message.threadId || "")
  const timeZone = String(message.timeZone || "UTC")
  const lastMessageId = String(message.lastMessageId || "")
  if (
    !/^[a-f0-9]{1,32}$/.test(threadId) ||
    !/^[a-f0-9]{0,32}$/.test(lastMessageId) ||
    !/^[A-Za-z0-9_+\-/]{1,64}$/.test(timeZone)
  )
    return { ok: false, code: "invalid_request" }
  await storageReady
  let s: Session
  try {
    s = await activeSession()
  } catch {
    return { ok: false, code: "login_required" }
  }
  // Badges are only for the Gmail account Threadly is connected to.
  if (
    typeof message.accountEmail !== "string" ||
    message.accountEmail.toLowerCase() !== s.user.email.toLowerCase()
  )
    return { ok: false, code: "account_mismatch" }
  // Kept for this browser session until valid_until, so reopening Gmail
  // reuses results. Labels and IDs only; a new message changes the key.
  const key = `classification:${s.user.id}:${threadId}:${lastMessageId}:${timeZone}`
  const kept = (await chrome.storage.session.get(key))[key]
  if (kept && Date.parse(kept.valid_until) > Date.now())
    return { ok: true, data: kept }
  if (pauseLeft(s.user.id) > 0) return cooldown(s.user.id)
  if (classifying >= 2)
    await new Promise<void>((resolve) => classifyWaiting.push(resolve))
  classifying += 1
  try {
    // A pause may have started while this request waited for a slot.
    if (pauseLeft(s.user.id) > 0) return cooldown(s.user.id)
    const data = await transport(
      s.origin,
      `/threads/${threadId}/classification`,
      "POST",
      { time_zone: timeZone },
      s.jwt,
      60000
    )
    if (
      data?.thread_id === threadId &&
      Date.parse(data.valid_until) > Date.now()
    )
      await keepClassification(key, data)
    return { ok: true, data }
  } catch (error) {
    const wait = error.retryAfterSeconds
    if (wait) {
      const until = Date.now() + wait * 1000
      if (["gmail_rate_limited", "gmail_quota_exceeded"].includes(error.code))
        gmailPausedUntil.set(
          s.user.id,
          Math.max(gmailPausedUntil.get(s.user.id) || 0, until)
        )
      else if (
        ["classification_busy", "classification_provider_unavailable"].includes(
          error.code
        )
      )
        modelPausedUntil = Math.max(modelPausedUntil, until)
    }
    return {
      ok: false,
      code: error.code || "connection_failed",
      status: error.status,
      retryAfterSeconds: wait ?? null
    }
  } finally {
    classifying -= 1
    classifyWaiting.shift()?.()
  }
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  const fromGmail =
    sender.id === chrome.runtime.id &&
    Boolean(sender.tab?.id) &&
    Boolean(sender.tab.url?.startsWith("https://mail.google.com/"))
  if (message?.type === "OPEN_SIDE_PANEL" && fromGmail) {
    void chrome.sidePanel.open({ tabId: sender.tab.id })
    return false
  }
  // Gmail pages may ask for one thing only: a thread's badges.
  if (message?.type === "THREADLY_CLASSIFY" && fromGmail) {
    void classifyThread(message).then(respond)
    return true
  }
  if (
    message?.channel !== "threadly" ||
    !trustedSender(sender, chrome.runtime.id)
  )
    return false
  void (async () => {
    await storageReady
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
        return login(message.capabilities, message.expectedUserId)
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
            chrome.storage.local.remove("threadlySession")
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
            await chrome.storage.local.remove("threadlySession")
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
            previous.refresh_token || previous.jwt,
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
          await chrome.storage.local.remove("threadlySession")
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
        const generation = sessionGeneration
        let s: Session | undefined
        try {
          s = await activeSession()
          if (
            message.expectedUserId !== undefined &&
            message.expectedUserId !== s.user.id
          )
            throw Object.assign(
              new Error("Your account changed. Reload this draft."),
              { code: "session_changed", status: 409 }
            )
          return await transport(
            s.origin,
            message.path,
            message.method,
            message.body,
            s.jwt
          )
        } catch (error) {
          if (
            error.status === 401 &&
            s &&
            !(await discardSession(s, generation))
          )
            throw Object.assign(
              new Error("Your session changed. Please try again."),
              {
                code: "session_changed",
                status: 409
              }
            )
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
