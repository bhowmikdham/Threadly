import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { useState } from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { CalendarSetup } from "../components/CalendarSetup"
import { TaskCard } from "../components/TaskCard"
import {
  calendarEventsReadReady,
  schedulingReadiness
} from "../lib/scheduling-readiness"
import type { Capability } from "../lib/types"

const ready = (id: string): Capability => ({
  id,
  ready: true,
  enabled: true,
  status: "ready"
})
const base = [
  ready("gmail_read"),
  ready("calendar_read"),
  ready("calendar_list")
]
const calls: any[] = []

beforeEach(() => {
  calls.length = 0
  vi.mocked(chrome.runtime.sendMessage).mockReset()
})

describe("Calendar onboarding", () => {
  it("keeps free/busy scheduling separate from optional event-title consent", () => {
    expect(schedulingReadiness([ready("gmail_read")], "missing").state).toBe(
      "connect"
    )
    expect(schedulingReadiness(base, "missing").state).toBe("preferences")
    expect(schedulingReadiness(base, "ready").state).toBe("ready")
    expect(calendarEventsReadReady(base)).toBe(false)
    expect(calendarEventsReadReady([ready("calendar_events_read")])).toBe(false)
    expect(
      calendarEventsReadReady([...base, ready("calendar_events_read")])
    ).toBe(true)
  })

  it("connects Calendar and saves only the user's chosen calendars and hours", async () => {
    vi.mocked(chrome.runtime.sendMessage).mockImplementation(
      async (message: any) => {
        calls.push(message)
        if (message.type === "LOGIN") return { ok: true, data: {} } as any
        if (message.path === "/calendar/calendars")
          return {
            ok: true,
            data: {
              calendars: [
                { id: "primary", summary: "Personal", can_read_busy: true },
                { id: "team", summary: "Team", can_read_busy: true }
              ]
            }
          } as any
        if (
          message.path === "/calendar/preferences" &&
          message.method === "GET"
        )
          return {
            ok: false,
            error: { code: "calendar_preferences_missing", message: "Missing" }
          } as any
        if (
          message.path === "/calendar/preferences" &&
          message.method === "PUT"
        )
          return {
            ok: true,
            data: { version: 1, preferences: message.body.preferences }
          } as any
        throw new Error(`Unexpected ${message.path}`)
      }
    )
    const onPreferences = vi.fn()
    function Example() {
      const [caps, setCaps] = useState([ready("gmail_read")])
      const [state, setState] = useState<"missing" | "ready">("missing")
      return (
        <CalendarSetup
          capabilities={caps}
          preferencesState={state}
          onAuth={async () => setCaps(base)}
          onPreferences={(value) => {
            onPreferences(value)
            setState(value ? "ready" : "missing")
          }}
        />
      )
    }
    render(<Example />)
    fireEvent.click(screen.getByRole("button", { name: "Connect Calendar" }))
    await screen.findByText("Personal")
    expect(calls.find((call) => call.type === "LOGIN").capabilities).toEqual([
      "calendar_read"
    ])
    fireEvent.click(screen.getByLabelText("Personal"))
    fireEvent.change(screen.getByLabelText("Timezone"), {
      target: { value: "Australia/Melbourne" }
    })
    fireEvent.click(
      screen.getByRole("button", { name: "Save scheduling preferences" })
    )
    await screen.findByText(/Scheduling preferences saved/)
    const saved = calls.find(
      (call) => call.path === "/calendar/preferences" && call.method === "PUT"
    )
    expect(saved.body.expected_version).toBe(0)
    expect(saved.body.preferences.calendar_ids).toEqual(["primary"])
    expect(saved.body.preferences.timezone).toBe("Australia/Melbourne")
    expect(saved.body.preferences.working_periods).toHaveLength(5)
    expect(onPreferences).toHaveBeenCalledWith(
      expect.objectContaining({ version: 1 })
    )
    expect(
      calls.some((call) => /action|booking|send/.test(call.path || ""))
    ).toBe(false)
  })

  it("asks separately for event titles and labels incomplete agenda coverage", async () => {
    vi.mocked(chrome.runtime.sendMessage).mockImplementation(
      async (message: any) => {
        calls.push(message)
        if (message.type === "LOGIN") return { ok: true, data: {} } as any
        if (message.path === "/calendar/agenda?period=today")
          return {
            ok: true,
            data: {
              timezone: "Australia/Melbourne",
              start: "2026-09-28T00:00:00+10:00",
              end: "2026-09-29T00:00:00+10:00",
              checked_at: "2026-09-28T00:00:00Z",
              coverage: "partial",
              calendars: [
                {
                  calendar_id: "primary",
                  name: "Personal",
                  status: "known",
                  reason: null,
                  events: [
                    {
                      summary: "Planning",
                      start: "2026-09-28T09:00:00+10:00",
                      end: "2026-09-28T09:30:00+10:00",
                      all_day: false,
                      location: null
                    }
                  ]
                },
                {
                  calendar_id: "team",
                  name: "Team",
                  status: "unknown",
                  reason: "provider_error",
                  events: []
                }
              ]
            }
          } as any
        throw new Error(`Unexpected ${message.path}`)
      }
    )
    function Example() {
      const [caps, setCaps] = useState(base)
      return (
        <CalendarSetup
          capabilities={caps}
          preferencesState="ready"
          onAuth={async () => setCaps([...base, ready("calendar_events_read")])}
          onPreferences={vi.fn()}
        />
      )
    }
    render(<Example />)
    fireEvent.click(
      screen.getByRole("button", { name: "Connect event read access" })
    )
    await screen.findByRole("button", { name: "View events" })
    expect(calls.find((call) => call.type === "LOGIN").capabilities).toEqual([
      "calendar_events_read"
    ])
    fireEvent.click(screen.getByRole("button", { name: "View events" }))
    await screen.findByText("Planning")
    expect(screen.getByText(/partial coverage/)).toBeTruthy()
    expect(
      screen.getByText(/Team: Events could not be read right now/)
    ).toBeTruthy()
    expect(
      calls.some((call) => call.method === "POST" || call.method === "PUT")
    ).toBe(false)
  })

  it("does not carry one account's selected calendars to another account", async () => {
    vi.mocked(chrome.runtime.sendMessage).mockImplementation(
      async (message: any) => {
        if (message.path === "/calendar/calendars")
          return {
            ok: true,
            data: {
              calendars: [
                { id: "primary", summary: "Personal", can_read_busy: true }
              ]
            }
          } as any
        if (message.path === "/calendar/preferences")
          return {
            ok: false,
            error: { code: "calendar_preferences_missing", message: "Missing" }
          } as any
        throw new Error(`Unexpected ${message.path}`)
      }
    )
    const element = (userId: number) => (
      <CalendarSetup
        key={userId}
        capabilities={base}
        preferencesState="missing"
        onAuth={vi.fn()}
        onPreferences={vi.fn()}
      />
    )
    const view = render(element(1))
    fireEvent.click(
      screen.getByRole("button", { name: "Load calendars and preferences" })
    )
    await screen.findByLabelText("Personal")
    fireEvent.click(screen.getByLabelText("Personal"))
    expect(
      (screen.getByLabelText("Personal") as HTMLInputElement).checked
    ).toBe(true)
    view.rerender(element(2))
    expect(
      screen.getByRole("button", { name: "Load calendars and preferences" })
    ).toBeTruthy()
    expect(screen.queryByLabelText("Personal")).toBeNull()
    await waitFor(() =>
      expect(screen.getByText("See upcoming events")).toBeTruthy()
    )
  })

  it("opens Calendar setup from a scheduling failure without submitting a write", () => {
    const openCalendarSetup = vi.fn()
    render(
      <TaskCard
        entry={{
          id: "request-1",
          instruction: "Find a meeting time tomorrow",
          errorCode: "calendar_preferences_missing",
          error: "Calendar preferences are missing."
        }}
        controller={{ openCalendarSetup }}
      />
    )
    fireEvent.click(screen.getByRole("button", { name: "Open Calendar setup" }))
    expect(openCalendarSetup).toHaveBeenCalledTimes(1)
    expect(chrome.runtime.sendMessage).not.toHaveBeenCalled()
  })
})
