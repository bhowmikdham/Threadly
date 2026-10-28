import { webcrypto } from "node:crypto"
import { describe, expect, it } from "vitest"

import {
  ACK_CAP,
  DAY,
  incomingMetadata,
  possibleSecuritySubject,
  RETENTION,
  SecurityAwarenessStore
} from "../lib/security-awareness"

function fixture() {
  let now = Date.UTC(2026, 9, 23),
    value: any,
    failRead = false,
    failWrite = false
  const store = {
    read: async () => {
      if (failRead) throw Error("unavailable")
      return structuredClone(value)
    },
    write: async (s: any) => {
      if (failWrite) throw Error("unavailable")
      value = structuredClone(s)
    }
  }
  const clock = () => now
  const make = () =>
    new SecurityAwarenessStore(store, clock, webcrypto as Crypto)
  const meta = (id = "abc123", at = now) => ({
    message_id: id,
    received_at: new Date(at).toISOString(),
    timestamp_source: "gmail.internalDate" as const
  })
  return {
    make,
    meta,
    advance: (n: number) => (now += n),
    now: clock,
    read: () => value,
    replace: (s: any) => (value = s),
    failRead: () => (failRead = true),
    failWrite: () => (failWrite = true)
  }
}

describe("tentative local subject cues", () => {
  it.each([
    "Security alert",
    "Critical security alert: check your account",
    "New sign-in detected",
    "Unrecognised login",
    "Suspicious account activity",
    "Your password was changed",
    "New device sign-in"
  ])("recognises an explicit cue: %s", (subject) =>
    expect(possibleSecuritySubject(subject)).toBe(true)
  )
  it.each([
    "IT support request",
    "High priority invoice",
    "Re: Security alert",
    "Fwd: New sign-in",
    "Security alert newsletter",
    "Security alerts weekly digest",
    "Security training",
    "New sign-in webinar",
    "Please review security",
    "<img src=x onerror=alert(1)>",
    "Security alert\ntraining"
  ])("ignores indirect/forwarded/promotional text: %s", (subject) =>
    expect(possibleSecuritySubject(subject)).toBe(false)
  )
})

describe("synthetic indirect access notices (not the actual Hertz wording)", () => {
  it.each([
    "Hertz: Confirm your sign-in",
    "Your Hertz sign-in verification code",
    "Your login code",
    "Account access verification",
    "Sign in confirmation",
    "Sign-in successful",
    "You signed in to Hertz",
    "You have logged in",
    "Your one-time sign-in link"
  ])("recognises account access: %s", (subject) =>
    expect(possibleSecuritySubject(subject)).toBe(true)
  )
  it.each([
    "Hertz rental reminder",
    "Confirm your Hertz reservation",
    "Your booking verification code",
    "Your verification code",
    "Confirm your account details",
    "Login to view your booking confirmation",
    "Sign-in verification tips",
    "Your login code sale",
    "Fwd: Hertz: Confirm your sign-in",
    "Re: Sign-in confirmation",
    "Sign-in verification guide",
    "Hertz offers just for you"
  ])("leaves ordinary/ambiguous mail alone: %s", (subject) =>
    expect(possibleSecuritySubject(subject)).toBe(false)
  )
})

it("returns the saved first disposition independently of prompt eligibility", async () => {
  const f = fixture(),
    s = f.make(),
    metadata = f.meta()
  const offered = await s.status("account-a", metadata)
  expect(offered.state).toBeNull()
  const saved = await s.status("account-a", metadata, "recognised")
  expect(saved.state).toBe("recognised")
  expect((await s.status("account-a", metadata, "dismissed")).state).toBe(
    "recognised"
  )
  expect(await s.check("account-a", metadata)).toBeNull()
  f.advance(DAY + 1)
  expect(await f.make().status("account-a", metadata)).toEqual(saved)
  expect(await f.make().check("account-a", metadata)).toBeNull()
  expect(await f.make().status("account-b", metadata)).toBeNull()
  expect(await f.make().status("account-a", f.meta("fff"))).not.toEqual(saved)
  // Changed receipt metadata must not resurrect an old acknowledgement.
  expect(await f.make().status("account-a", f.meta())).toBeNull()
})

it("requires bound provider metadata and rejects missing/forged timestamp sources", () => {
  const data = {
    thread_id: "aaa",
    source: "live_gmail",
    persisted: false,
    source_message_ids: ["bbb"],
    latest_incoming: {
      message_id: "bbb",
      received_at: "2026-10-23T01:00:00Z",
      timestamp_source: "gmail.internalDate"
    }
  }
  expect(incomingMetadata(data, "aaa", "bbb")).toEqual(data.latest_incoming)
  for (const bad of [
    null,
    { ...data, thread_id: "ccc" },
    { ...data, source_message_ids: [] },
    {
      ...data,
      latest_incoming: {
        ...data.latest_incoming,
        timestamp_source: "header.Date"
      }
    },
    {
      ...data,
      latest_incoming: { ...data.latest_incoming, received_at: "2026-10-23" }
    }
  ])
    expect(incomingMetadata(bad, "aaa", "bbb")).toBeNull()
  expect(incomingMetadata(data, "aaa", "ccc")).toBeNull()
})

it("offers recent pre-installation mail on first use and after a restart", async () => {
  const f = fixture(),
    metadata = f.meta("aaa", f.now() - 4 * 3600000),
    offered = await f.make().check("account-a", metadata)
  expect(offered).not.toBeNull()
  expect(await f.make().check("account-a", metadata)).toEqual(offered)
})

it("enforces the rolling 24-hour boundary and ignores future, missing and invalid dates", async () => {
  const f = fixture(),
    s = f.make()
  expect(
    await s.check("account-a", f.meta("aaa", f.now() - DAY + 1))
  ).not.toBeNull()
  expect(await s.check("account-a", f.meta())).not.toBeNull()
  for (const metadata of [
    null,
    f.meta("bbb", f.now() + 1),
    f.meta("ccc", f.now() - DAY),
    f.meta("ddd", f.now() - 31 * DAY),
    { ...f.meta(), received_at: "invalid" }
  ])
    expect(await s.check("account-a", metadata)).toBeNull()
})

it.each(["recognised", "dismissed"] as const)(
  "preserves existing v1 %s state while allowing unacknowledged mail before its saved activation time",
  async (state) => {
    const f = fixture(),
      s = f.make()
    await s.check("account-a", null)
    f.advance(1000)
    const acknowledged = f.meta()
    await s.check("account-a", acknowledged, state)
    const previous = structuredClone(f.read()),
      recent = f.meta("def456", f.now() - 4 * 3600000),
      restarted = f.make()
    expect(Date.parse(recent.received_at)).toBeLessThan(
      previous.accounts[0].activatedAt
    )
    expect(await restarted.check("account-a", recent)).not.toBeNull()
    expect(await restarted.check("account-a", acknowledged)).toBeNull()
    expect(f.read().version).toBe(1)
    expect(f.read().salt).toEqual(previous.salt)
    expect(f.read().acknowledgements).toEqual(previous.acknowledgements)
    expect(f.read().accounts[0].activatedAt).toBe(
      previous.accounts[0].activatedAt
    )
  }
)

it.each(["recognised", "dismissed"] as const)(
  "persists %s across refresh/service-worker restart and isolates accounts/new messages",
  async (state) => {
    const f = fixture(),
      s = f.make()
    await s.check("account-a", null)
    await s.check("account-b", null)
    f.advance(1000)
    const metadata = f.meta(),
      offered = await s.check("account-a", metadata)
    expect(await s.check("account-a", metadata, state)).toEqual(offered)
    expect(await f.make().check("account-a", metadata)).toBeNull()
    expect(await f.make().check("account-a", metadata, state)).toEqual(offered)
    expect((await s.check("account-b", metadata))?.key).not.toBe(offered.key)
    expect(await s.check("account-a", f.meta("def456"))).not.toBeNull()
    const persisted = JSON.stringify(f.read())
    for (const forbidden of [
      "account-a",
      "account-b",
      "abc123",
      "def456",
      "subject",
      "sender",
      "body",
      "snippet",
      "thread_id"
    ])
      expect(persisted).not.toContain(forbidden)
    expect(f.read().acknowledgements[0].state).toBe(state)
  }
)

it("fails closed on storage read/write failure, corruption and clock rollback", async () => {
  for (const mode of ["failRead", "failWrite"] as const) {
    const f = fixture()
    f[mode]()
    await expect(f.make().check("account", f.meta())).rejects.toThrow(
      "unavailable"
    )
  }
  const f = fixture(),
    s = f.make()
  await s.check("account", null)
  f.advance(1000)
  f.failWrite()
  await expect(s.check("account", f.meta(), "recognised")).rejects.toThrow()
  expect(f.read().acknowledgements).toHaveLength(0)
  const g = fixture()
  g.replace({ version: 1 })
  await expect(g.make().check("account", null)).rejects.toThrow()
  const h = fixture()
  await h.make().check("account", null)
  h.advance(-1)
  await expect(h.make().check("account", null)).rejects.toThrow()
})

it("serialises concurrent acknowledgements and prevents re-prompt after 30-day expiry", async () => {
  const f = fixture(),
    s = f.make()
  await s.check("account", null)
  f.advance(1000)
  await Promise.all([
    s.check("account", f.meta(), "recognised"),
    s.check("account", f.meta("def456"), "dismissed")
  ])
  expect(f.read().acknowledgements).toHaveLength(2)
  const old = f.meta()
  f.advance(RETENTION + 1)
  expect(await f.make().check("account", old)).toBeNull()
  expect(f.read().acknowledgements).toHaveLength(0)
  expect(f.read().accounts).toHaveLength(1)
  f.advance(1)
  expect(await s.check("account", f.meta("fff111"))).not.toBeNull()
})

it("suppresses new prompts at capacity without evicting active acknowledgements", async () => {
  const f = fixture(),
    s = f.make()
  await s.check("account", null)
  f.advance(1000)
  const state = f.read()
  state.acknowledgements = Array.from({ length: ACK_CAP }, (_, i) => ({
    key: i.toString(16).padStart(64, "0"),
    receivedAt: f.now(),
    acknowledgedAt: f.now(),
    state: "dismissed"
  }))
  f.replace(state)
  expect(await s.check("account", f.meta())).toBeNull()
  expect(f.read().acknowledgements).toHaveLength(ACK_CAP)
})

it("keeps one first acknowledgement under concurrent conflicting responses", async () => {
  const f = fixture(),
    s = f.make()
  await s.check("account", null)
  f.advance(1)
  const responses = await Promise.all([
    s.check("account", f.meta(), "recognised"),
    s.check("account", f.meta(), "dismissed")
  ])
  expect(responses[0]).toEqual(responses[1])
  expect(f.read().acknowledgements).toHaveLength(1)
  expect(f.read().acknowledgements[0].state).toBe("recognised")
})

it("purges expired references at worker startup without creating new state", async () => {
  const f = fixture(),
    s = f.make()
  await s.prune()
  expect(f.read()).toBeUndefined()
  await s.check("account", null)
  f.advance(1)
  await s.check("account", f.meta(), "recognised")
  f.advance(RETENTION + 1)
  await f.make().prune()
  expect(f.read().accounts).toHaveLength(0)
  expect(f.read().acknowledgements).toHaveLength(0)
})
