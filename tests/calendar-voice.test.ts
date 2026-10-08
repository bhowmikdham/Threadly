import { beforeEach, expect, it, vi } from "vitest"

import { calendarVoiceReply } from "../lib/calendar-voice"
import { parseConfirmation } from "../lib/spoken-reply"
import type { CalendarAction } from "../lib/types"

const base: CalendarAction = {
  action_id: "e1",
  state: "proposed",
  version: 1,
  payload_hash: "h1",
  approval_available: true,
  blockers: [],
  authorization: "separate_exact_event_approval",
  preview: {
    calendar_id: "w",
    send_updates: "none",
    event: {
      summary: "Focus",
      description: "",
      location: "",
      attendees: [],
      start: {
        dateTime: "2026-10-06T03:00:00Z",
        timeZone: "Australia/Melbourne"
      },
      end: { dateTime: "2026-10-06T03:30:00Z", timeZone: "Australia/Melbourne" }
    }
  }
}
const send = () => chrome.runtime.sendMessage as any
const reply = (data: unknown) => ({ ok: true, data })
const approved = () =>
  send().mock.calls.some((c: any) => c[0].path.endsWith("/approve"))
beforeEach(() => vi.clearAllMocks())

it("only accepts a plain yes or no", () => {
  expect(parseConfirmation("Yes.")).toBe("yes")
  expect(parseConfirmation("create event")).toBe("yes")
  expect(parseConfirmation("No thanks")).toBe("no")
  expect(parseConfirmation("yes but move it to 4pm")).toBeNull()
})

it("asks aloud, then approves the exact version it read out on yes", async () => {
  send().mockImplementation(async (m: any) =>
    m.path.endsWith("/approve")
      ? reply({ ...base, state: "succeeded", version: 2 })
      : reply(base)
  )
  const r = await calendarVoiceReply("e1")
  expect(r.showChat).toBe(true)
  expect(r.text).toContain("Shall I create it?")
  expect(approved()).toBe(false)
  const done = await r.confirm!.onYes()
  const call = send().mock.calls.find((c: any) =>
    c[0].path.endsWith("/approve")
  )
  expect(call[0].body).toMatchObject({
    expected_version: 1,
    payload_hash: "h1"
  })
  expect(done.text).toContain("added it to your calendar")
})

it("does not create when the event changed after it was read", async () => {
  let n = 0
  send().mockImplementation(async () =>
    reply(++n === 1 ? base : { ...base, version: 2, payload_hash: "h2" })
  )
  const r = await calendarVoiceReply("e1")
  expect((await r.confirm!.onYes()).text).toContain("changed")
  expect(approved()).toBe(false)
})

it("rejects on no, and says manual approval when approval is unavailable", async () => {
  send().mockImplementation(async () => reply(base))
  const r = await calendarVoiceReply("e1")
  await r.confirm!.onNo()
  expect(
    send().mock.calls.some((c: any) => c[0].path.endsWith("/reject"))
  ).toBe(true)
  send().mockImplementation(async () =>
    reply({ ...base, approval_available: false })
  )
  const m = await calendarVoiceReply("e1")
  expect(m.confirm).toBeUndefined()
  expect(m.text).toContain("Manual approval is required")
})