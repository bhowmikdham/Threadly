import { webcrypto } from "node:crypto"
import { afterEach, expect, it, vi } from "vitest"

import { recognitionLabels } from "../lib/classification"

const clone = (value: any) =>
  value === undefined ? undefined : structuredClone(value)
afterEach(() => vi.unstubAllGlobals())
it("reconciles cached/late classifier results with saved recognition and isolates mailbox/server scopes", async () => {
  vi.stubGlobal("crypto", webcrypto)
  const now = Date.now()
  const local: any = {
    threadlySession: {
      jwt: `h.${btoa(JSON.stringify({ exp: Math.floor(now / 1000) + 3600 }))}.s`,
      refresh_token: "synthetic",
      user: { id: 1, email: "me@example.test", name: null },
      origin: "http://127.0.0.1:8000"
    },
    threadlyInboxMarks: { enabled: true, words: true }
  }
  const session: any = {}
  const area = (values: any) => ({
    get: vi.fn(async (key?: string) =>
      key ? { [key]: clone(values[key]) } : clone(values)
    ),
    set: vi.fn(async (items: any) => {
      Object.assign(values, clone(items))
    }),
    remove: vi.fn(async (keys: string | string[]) => {
      for (const key of Array.isArray(keys) ? keys : [keys]) delete values[key]
    }),
    setAccessLevel: vi.fn(async () => {})
  })
  let listeners: any[] = []
  vi.stubGlobal("chrome", {
    runtime: {
      id: "extension",
      onInstalled: { addListener: vi.fn() },
      onMessage: { addListener: (listener: any) => listeners.push(listener) }
    },
    storage: {
      local: area(local),
      session: area(session),
      onChanged: { addListener: vi.fn() }
    },
    tabs: {
      query: vi.fn(async () => [{ id: 1 }]),
      sendMessage: vi.fn(async () => {})
    }
  })
  const data = {
    schema_version: "email-classification-with-action.v1",
    thread_id: "aaa",
    status: "classified",
    labels: {
      priority: "High",
      category: "it",
      needs_reply: false,
      action: "review"
    },
    valid_until: new Date(now + 3600000).toISOString(),
    source: "live_gmail",
    persisted: false,
    source_message_ids: ["bbb"],
    latest_incoming: {
      message_id: "bbb",
      received_at: new Date(now).toISOString(),
      timestamp_source: "gmail.internalDate"
    },
    security_priority: {
      message_id: "bbb",
      priority_if_recognised: "Low",
      evidence: ["bbb"]
    }
  }
  const releases: ((response: Response) => void)[] = []
  const fetchMock = vi.fn(
    () => new Promise<Response>((resolve) => releases.push(resolve))
  )
  vi.stubGlobal("fetch", fetchMock)
  await import("../background")
  const sender = {
    id: "extension",
    tab: { id: 1, url: "https://mail.google.com/" }
  }
  const message = {
    type: "THREADLY_CLASSIFY",
    threadId: "aaa",
    lastMessageId: "bbb",
    timeZone: "UTC",
    accountEmail: "me@example.test"
  }
  const request = (input = message): Promise<any> =>
    new Promise((resolve) => {
      expect(listeners[0](input, sender, resolve)).toBe(true)
    })
  const first = request(),
    late = request()
  await vi.waitFor(() => expect(releases).toHaveLength(2))
  releases[0](new Response(JSON.stringify(data)))
  const offered = await first
  expect(offered.awareness).not.toBeNull()
  expect(offered.security.state).toBeNull()
  const ack = await request({
    ...message,
    type: "THREADLY_SECURITY_ACK",
    state: "recognised"
  } as any)
  expect(ack).toMatchObject({
    ok: true,
    state: "recognised",
    key: offered.awareness.key
  })
  releases[1](new Response(JSON.stringify(data)))
  const stale = await late
  expect(stale.data.labels.priority).toBe("High")
  expect(stale.security.state).toBe("recognised")
  expect(stale.awareness).toBeNull()
  expect(
    recognitionLabels(stale.data, stale.security.state === "recognised")
      .priority
  ).toBe("Low")
  // Cached results do not bypass saved recognition, even after a service worker restart.
  vi.resetModules()
  listeners = []
  await import("../background")
  const cached = await request()
  expect(cached.security.state).toBe("recognised")
  expect(fetchMock).toHaveBeenCalledTimes(2)
  const conflict = await request({
    ...message,
    type: "THREADLY_SECURITY_ACK",
    state: "dismissed"
  } as any)
  expect(conflict.state).toBe("recognised")
  // Same numeric user/thread/message IDs in another mailbox or server cannot reuse cache/ack.
  fetchMock.mockImplementation(async () => new Response(JSON.stringify(data)))
  local.threadlySession.user.email = "other@example.test"
  expect((await request()).code).toBe("account_mismatch")
  const other = await request({
    ...message,
    accountEmail: "other@example.test"
  })
  expect(other.security.state).toBeNull()
  expect(other.security.key).not.toBe(cached.security.key)
  local.backendOrigin = "http://localhost:9000"
  local.threadlySession.origin = local.backendOrigin
  const server = await request({
    ...message,
    accountEmail: "other@example.test"
  })
  expect(server.security.state).toBeNull()
  expect(server.security.key).not.toBe(other.security.key)
  expect(fetchMock).toHaveBeenCalledTimes(4)
  const persisted = JSON.stringify(local.threadlySecurityAwarenessV1)
  for (const forbidden of [
    "aaa",
    "bbb",
    "example.test",
    "subject",
    "body",
    "evidence",
    "priority"
  ])
    expect(persisted).not.toContain(forbidden)
})
