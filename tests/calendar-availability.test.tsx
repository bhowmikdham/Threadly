import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { CalendarAvailabilityCard } from "../components/CalendarAvailabilityCard"
import { TaskCard } from "../components/TaskCard"
import { calendarAvailability } from "../lib/calendar-availability"
import { useAssistant } from "../lib/use-assistant"

const details = (extra: any = {}) => ({
  operation: "check_time_availability",
  availability: "busy",
  scope: "selected_calendars",
  start: "2026-10-07T06:00:00Z",
  end: "2026-10-07T06:30:00Z",
  timezone: "Australia/Melbourne",
  duration_minutes: 30,
  duration_source: "saved_default",
  complete: true,
  coverage: "complete",
  checked_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 300_000).toISOString(),
  busy_periods: [
    { start: "2026-10-07T06:00:00Z", end: "2026-10-07T06:30:00Z" }
  ],
  ...extra
})
beforeEach(() => {
  ;(chrome.storage as any).session = {
    get: vi.fn().mockResolvedValue({}),
    set: vi.fn(),
    remove: vi.fn()
  }
})
afterEach(() => vi.useRealTimers())

describe("availability card", () => {
  it("keeps the reply beside an accessible Calendar card, with no creation controls", () => {
    const refresh = vi.fn()
    render(
      <TaskCard
        entry={{
          id: "1",
          instruction: "am I free at 5pm tomorrow",
          message: "No, you're busy tomorrow at 5 pm.",
          calendarAvailability: calendarAvailability(details())
        }}
        controller={{
          canRecheckAvailability: () => true,
          recheckAvailability: refresh
        }}
      />
    )
    expect(screen.getByText("No, you're busy tomorrow at 5 pm.")).toBeTruthy()
    expect(
      screen.getByRole("region", { name: "Calendar availability" })
    ).toBeTruthy()
    expect(screen.getByRole("heading", { name: "Busy" })).toBeTruthy()
    expect(
      screen.getByText(/Australia\/Melbourne · Selected calendars/)
    ).toBeTruthy()
    expect(screen.getByText(/30-minute slot · Saved default/)).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Create event" })).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Check again" }))
    expect(refresh).toHaveBeenCalledOnce()
  })
  it.each(["busy", "unknown"])(
    "retains incomplete coverage with %s evidence",
    (availability) => {
      const value = calendarAvailability(
        details({
          availability,
          complete: false,
          coverage: "unknown",
          busy_periods: availability === "busy" ? details().busy_periods : []
        })
      )!
      render(
        <CalendarAvailabilityCard
          value={value}
          enabled={false}
          onRecheck={vi.fn()}
        />
      )
      expect(
        screen.getByText(/Some selected calendars couldn't be checked/)
      ).toBeTruthy()
      expect(
        screen
          .getByRole("button", { name: "Check again" })
          .hasAttribute("disabled")
      ).toBe(true)
      expect(screen.queryByRole("heading", { name: "Free" })).toBeNull()
    }
  )
  it("expires a displayed free result and updates again when props change", () => {
    vi.useFakeTimers()
    const value = calendarAvailability(
      details({
        availability: "free",
        busy_periods: [],
        expires_at: new Date(Date.now() + 1_000).toISOString()
      })
    )!
    const view = render(
      <CalendarAvailabilityCard value={value} enabled onRecheck={vi.fn()} />
    )
    expect(screen.getByRole("heading", { name: "Free" })).toBeTruthy()
    act(() => vi.advanceTimersByTime(1_100))
    expect(
      screen.getByRole("heading", { name: "Previously: free" })
    ).toBeTruthy()
    view.rerender(
      <CalendarAvailabilityCard
        value={calendarAvailability(details())!}
        enabled
        onRecheck={vi.fn()}
      />
    )
    expect(screen.getByRole("heading", { name: "Busy" })).toBeTruthy()
  })
  it("ignores old/invalid payloads and never presents incomplete coverage as free", () => {
    expect(
      calendarAvailability({ operation: "search_calendar_events" })
    ).toBeUndefined()
    expect(
      calendarAvailability(details({ timezone: "invalid/private" }))
    ).toBeUndefined()
    expect(calendarAvailability(details({ end: "bad" }))).toBeUndefined()
    expect(
      calendarAvailability(
        details({ availability: "free", complete: false, busy_periods: [] })
      )?.availability
    ).toBe("unknown")
  })
  it("refreshes only the current conversation's latest check using a new read turn", async () => {
    const calls: any[] = []
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((async (
      m: any
    ) => {
      calls.push(m)
      return {
        ok: true,
        data: {
          conversation_id: m.body.conversation_id,
          version: m.body.expected_version + 1,
          kind: "message",
          text: "Checked your availability.",
          calendar_tools: details({ availability: "free", busy_periods: [] })
        }
      }
    }) as any)
    const hook = renderHook(() =>
      useAssistant({ id: 1, email: "me@example.test", name: "Tester" })
    )
    await waitFor(() => expect(hook.result.current.restoring).toBe(false))
    await act(async () =>
      hook.result.current.submit("am I free at 5pm tomorrow")
    )
    const first = hook.result.current.entries[0]
    expect(first.calendarAvailability?.availability).toBe("free")
    expect(hook.result.current.canRecheckAvailability(first)).toBe(true)
    await act(async () => hook.result.current.recheckAvailability(first))
    expect(calls).toHaveLength(2)
    expect(calls[1].path).toBe("/assistant/conversation-turns")
    expect(calls[1].body.instruction).toBe("Check that availability again")
    expect(calls[1].body.request_id).not.toBe(calls[0].body.request_id)
    expect(calls[1].body.expected_version).toBe(1)
    expect(hook.result.current.canRecheckAvailability(first)).toBe(false)
    await act(async () => hook.result.current.recheckAvailability(first))
    expect(calls).toHaveLength(2)
  })
})
