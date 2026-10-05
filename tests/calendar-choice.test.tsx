import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor
} from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { CalendarChoiceCard } from "../components/CalendarChoiceCard"
import type { CalendarChoices } from "../lib/types"
import { useAssistant } from "../lib/use-assistant"

const user = { id: 1, email: "me@example.test", name: "Tester" }
const options = (): CalendarChoices => ({
  expires_at: new Date(Date.now() + 60_000).toISOString(),
  choices: [
    { choice_id: "choice-1", label: "Personal calendar", access: "editable" },
    { choice_id: "choice-2", label: "Family", access: "editable" }
  ]
})
beforeEach(() => {
  ;(chrome.storage as any).session = {
    get: vi.fn().mockResolvedValue({}),
    set: vi.fn().mockResolvedValue(undefined),
    remove: vi.fn().mockResolvedValue(undefined)
  }
})
async function picker(select: (m: any, first: any) => any, value = options()) {
  const calls: any[] = []
  vi.mocked(chrome.runtime.sendMessage).mockImplementation((async (m: any) => {
    calls.push(m)
    if (m.path === "/assistant/conversation-turns")
      return {
        ok: true,
        data: {
          conversation_id: m.body.conversation_id,
          version: m.body.expected_version + 1,
          kind: "clarification",
          text: "Which calendar should I use?",
          calendar_choices: value
        }
      }
    return select(m, calls[0])
  }) as any)
  const hook = renderHook(() => useAssistant(user))
  await waitFor(() => expect(hook.result.current.restoring).toBe(false))
  await act(async () => {
    await hook.result.current.submit(
      "could you create Meeting at 4 pm tomorrow"
    )
  })
  return { ...hook, calls }
}
const selected = (m: any, first: any, extra: any = {}) => ({
  ok: true,
  data: {
    conversation_id: first.body.conversation_id,
    version: m.body.expected_version + 1,
    kind: "calendar_event",
    calendar_action_id: "action-1",
    calendar_action: {
      action_id: "action-1",
      state: "proposed",
      preview: {
        calendar_name: "Personal calendar",
        event: {
          summary: "Meeting",
          start: {
            dateTime: "2026-10-06T05:00:00Z",
            timeZone: "Australia/Melbourne"
          }
        }
      }
    },
    ...extra
  }
})

describe("structured Calendar choices", () => {
  it("offers only editable buttons and sends opaque handles on keyboard/click selection", () => {
    const choose = vi.fn()
    const value = options()
    value.choices.push({
      choice_id: "readonly",
      label: "Holidays",
      access: "busy-time"
    } as any)
    render(<CalendarChoiceCard value={value} enabled choose={choose} />)
    expect(screen.queryByRole("button", { name: "Holidays" })).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Family" }))
    expect(choose).toHaveBeenCalledWith("choice-2")
    expect(screen.getByText(/approval setting still applies/)).toBeTruthy()
  })
  it("expires visible choices and rejects clicks after the deadline", () => {
    vi.useFakeTimers()
    try {
      const choose = vi.fn()
      render(<CalendarChoiceCard value={options()} enabled choose={choose} />)
      act(() => vi.advanceTimersByTime(60_001))
      expect(
        screen.getByRole("button", { name: "Family" }).hasAttribute("disabled")
      ).toBe(true)
      fireEvent.click(screen.getByRole("button", { name: "Family" }))
      expect(choose).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })
  it("sends only the issued ID/version, preserves returned slots, and does not approve", async () => {
    const { result, calls } = await picker(selected)
    const entry = result.current.entries[0]
    await act(async () => {
      await result.current.chooseCalendar(entry, "choice-1")
    })
    expect(calls[1].path).toBe(
      `/assistant/conversations/${calls[0].body.conversation_id}/calendar-choice`
    )
    expect(calls[1].body).toEqual({
      request_id: expect.any(String),
      expected_version: 1,
      choice_id: "choice-1"
    })
    expect(result.current.entries[1]).toMatchObject({
      instruction: "",
      calendarAction: {
        state: "proposed",
        preview: {
          event: {
            summary: "Meeting",
            start: { dateTime: "2026-10-06T05:00:00Z" }
          }
        }
      }
    })
    expect(result.current.canChooseCalendar(entry)).toBe(false)
    await act(async () => {
      await result.current.chooseCalendar(entry, "choice-2")
    })
    expect(calls).toHaveLength(2)
    expect(calls.some((call) => call.path.endsWith("/approve"))).toBe(false)
  })
  it("fences double clicks while selection is in flight", async () => {
    let finish: () => void
    const { result, calls } = await picker(
      (m, first) =>
        new Promise((resolve) => {
          finish = () => resolve(selected(m, first))
        })
    )
    const entry = result.current.entries[0]
    let first: Promise<void>
    await act(async () => {
      first = result.current.chooseCalendar(entry, "choice-1")
      await Promise.resolve()
      await result.current.chooseCalendar(entry, "choice-2")
    })
    expect(calls).toHaveLength(2)
    await act(async () => {
      finish()
      await first
    })
    expect(
      result.current.entries.filter((item) => item.calendarActionId)
    ).toHaveLength(1)
  })
  it("retains the exact choice request after a network failure and retries that endpoint", async () => {
    let fail = true
    const { result, calls } = await picker((m, first) => {
      if (fail) throw new Error("Connection lost")
      return selected(m, first)
    })
    const entry = result.current.entries[0]
    await act(async () => {
      await result.current.chooseCalendar(entry, "choice-1")
    })
    expect(result.current.contextLocked).toBe(true)
    await act(async () => {
      await result.current.chooseCalendar(entry, "choice-2")
    })
    fail = false
    await act(async () => {
      await result.current.retry(result.current.entries.at(-1))
    })
    expect(calls).toHaveLength(3)
    expect(calls[2]).toEqual(calls[1])
    expect(result.current.entries.at(-1).calendarActionId).toBe("action-1")
  })
  it("uses fresh returned choices when preferences changed, without automatic selection", async () => {
    const fresh = options()
    fresh.choices = [
      { choice_id: "choice-new", label: "Work", access: "editable" }
    ]
    const { result, calls } = await picker((m, first) =>
      selected(m, first, {
        kind: "clarification",
        calendar_action_id: undefined,
        calendar_action: undefined,
        error_code: "calendar_choices_changed",
        calendar_choices: fresh
      })
    )
    const previous = result.current.entries[0]
    await act(async () => {
      await result.current.chooseCalendar(previous, "choice-1")
    })
    expect(result.current.canChooseCalendar(previous)).toBe(false)
    const current = result.current.entries.at(-1)
    expect(current.calendarChoices).toEqual(fresh)
    expect(result.current.canChooseCalendar(current)).toBe(true)
    expect(calls).toHaveLength(2)
    expect(
      result.current.entries.every((entry) => !entry.calendarActionId)
    ).toBe(true)
  })
  it("rejects unknown, read-only, expired and changed-chat choices without API writes", async () => {
    const value = options()
    value.choices.push({
      choice_id: "readonly",
      label: "Holidays",
      access: "busy-time"
    } as any)
    const { result, calls } = await picker(selected, value)
    const entry = result.current.entries[0]
    await act(async () => {
      await result.current.chooseCalendar(entry, "unknown")
      await result.current.chooseCalendar(entry, "readonly")
      await result.current.chooseCalendar(
        {
          ...entry,
          calendarChoices: { ...value, expires_at: "2000-01-01T00:00:00Z" }
        },
        "choice-1"
      )
      await result.current.chooseCalendar(
        { ...entry, conversationId: "old-chat" },
        "choice-1"
      )
    })
    expect(calls).toHaveLength(1)
  })
  it("restores issued choices from the owned conversation without turning a label into text", async () => {
    ;(chrome.storage.session.get as any).mockResolvedValue({
      "threadlyConversation:1": { id: "chat-1", version: 3 }
    })
    const value = options()
    const calls: any[] = []
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((async (
      m: any
    ) => {
      calls.push(m)
      return {
        ok: true,
        data: {
          conversation_id: "chat-1",
          version: 3,
          history: [],
          calendar_choices: value
        }
      }
    }) as any)
    const { result } = renderHook(() => useAssistant(user))
    await waitFor(() => expect(result.current.restoring).toBe(false))
    expect(result.current.entries[0]).toMatchObject({
      instruction: "",
      calendarChoices: value
    })
    expect(result.current.canChooseCalendar(result.current.entries[0])).toBe(
      true
    )
    expect(calls).toHaveLength(1)
  })
  it("restores a failed structured choice and retries with its original request identity", async () => {
    const body = {
      conversation_id: "chat-1",
      expected_version: 3,
      request_id: "click-1",
      instruction: "",
      timezone: "Australia/Melbourne",
      calendar_choice_id: "choice-1"
    }
    ;(chrome.storage.session.get as any).mockResolvedValue({
      "threadlyConversation:1": {
        id: "chat-1",
        version: 3,
        pendingTurn: { id: "click-1", body }
      }
    })
    const calls: any[] = []
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((async (
      m: any
    ) => {
      calls.push(m)
      if (!m.body)
        return {
          ok: true,
          data: {
            conversation_id: "chat-1",
            version: 3,
            history: [],
            pending_request_id: "click-1",
            calendar_choices: options()
          }
        }
      return {
        ok: true,
        data: {
          conversation_id: "chat-1",
          version: 4,
          text: "Review the existing event."
        }
      }
    }) as any)
    const { result } = renderHook(() => useAssistant(user))
    await waitFor(() => expect(result.current.restoring).toBe(false))
    expect(new Set(result.current.entries.map((e) => e.id)).size).toBe(
      result.current.entries.length
    )
    await act(async () => {
      await result.current.retry(
        result.current.entries.find((e) => result.current.canRetry(e))
      )
    })
    expect(calls[1]).toMatchObject({
      path: "/assistant/conversations/chat-1/calendar-choice",
      body: {
        request_id: "click-1",
        expected_version: 3,
        choice_id: "choice-1"
      }
    })
    expect(result.current.contextLocked).toBe(false)
  })
})
