import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, expect, it, vi } from "vitest"

import { CalendarApprovalMenu } from "../components/CalendarApprovalMenu"
import { CalendarEventCard } from "../components/CalendarEventCard"
import { TaskCard } from "../components/TaskCard"
import type { CalendarAction } from "../lib/types"

const event: CalendarAction = {
  action_id: "event-1",
  state: "proposed",
  version: 1,
  payload_hash: "exact-hash",
  approval_available: true,
  blockers: [],
  authorization: "separate_exact_event_approval",
  preview: {
    calendar_id: "work",
    calendar_name: "Work",
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
beforeEach(() => vi.clearAllMocks())
it("persists explicit Always allow, uses version checks, and resets for a new chat", async () => {
  let mode = "ask",
    version = 0
  const remember = vi.fn().mockResolvedValue(undefined),
    saving = vi.fn()
  const calls: any[] = []
  vi.mocked(chrome.runtime.sendMessage).mockImplementation(async (m: any) => {
    calls.push(m)
    if (m.method === "PUT") {
      mode = m.body.mode
      version++
    }
    return {
      ok: true,
      data: { mode: m.path.includes("chat-2") ? "ask" : mode, version }
    }
  })
  const { rerender } = render(
    <CalendarApprovalMenu
      conversationId="chat-1"
      remember={remember}
      onSaving={saving}
    />
  )
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Calendar approval: Ask for approval"
    })
  )
  const always = screen.getByRole("menuitemradio", { name: /Always allow/ })
  expect(always.getAttribute("aria-checked")).toBe("false")
  fireEvent.click(always)
  await screen.findByRole("button", { name: "Calendar approval: Always allow" })
  expect(remember).toHaveBeenCalledOnce()
  expect(calls.find((c) => c.method === "PUT").body).toEqual({
    mode: "always",
    expected_version: 0
  })
  expect(saving.mock.calls).toEqual([[true], [false]])
  rerender(
    <CalendarApprovalMenu
      conversationId="chat-2"
      remember={remember}
      onSaving={saving}
    />
  )
  await screen.findByRole("button", {
    name: "Calendar approval: Ask for approval"
  })
})
it("does not grant when browser persistence fails and supports keyboard dismissal", async () => {
  const calls: any[] = []
  vi.mocked(chrome.runtime.sendMessage).mockImplementation(async (m: any) => {
    calls.push(m)
    return { ok: true, data: { mode: "ask", version: 0 } }
  })
  render(
    <CalendarApprovalMenu
      conversationId="chat"
      remember={vi.fn().mockRejectedValue(new Error("Couldn't save this chat"))}
      onSaving={vi.fn()}
    />
  )
  const trigger = await screen.findByRole("button", {
    name: "Calendar approval: Ask for approval"
  })
  fireEvent.click(trigger)
  fireEvent.click(screen.getByRole("menuitemradio", { name: /Always allow/ }))
  await screen.findByRole("alert")
  expect(calls.some((c) => c.method === "PUT")).toBe(false)
  fireEvent.keyDown(
    screen.getByRole("menuitemradio", { name: /Ask for approval/ }),
    { key: "Escape" }
  )
  expect(screen.queryByRole("menu")).toBeNull()
  expect(document.activeElement).toBe(trigger)
})
it("requires exact-payload approval and displays success only after server confirmation", async () => {
  const calls: any[] = []
  vi.mocked(chrome.runtime.sendMessage).mockImplementation(async (m: any) => {
    calls.push(m)
    return {
      ok: true,
      data: m.path.endsWith("/approve")
        ? { ...event, state: "succeeded", approval_available: false }
        : event
    }
  })
  render(<CalendarEventCard id="event-1" initial={event} />)
  await waitFor(() => expect(calls).toHaveLength(1))
  expect(screen.queryByText("Event created")).toBeNull()
  expect(screen.getByText("Australia/Melbourne · Work")).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: "Create event" }))
  await screen.findByText("Event created")
  expect(calls[1].body).toMatchObject({
    expected_version: 1,
    payload_hash: "exact-hash"
  })
  expect(screen.queryByRole("button", { name: "Create event" })).toBeNull()
})
it("restores current event state and never offers another insert after an uncertain outcome", async () => {
  vi.mocked(chrome.runtime.sendMessage).mockResolvedValue({
    ok: true,
    data: { ...event, state: "outcome_unknown", approval_available: false }
  })
  render(
    <TaskCard
      entry={{
        id: "restored",
        instruction: "Create Focus",
        message: "Review this event before creating it.",
        calendarActionId: "event-1"
      }}
      controller={{}}
    />
  )
  await screen.findByText("Waiting for confirmation")
  expect(screen.queryByText("Review this event before creating it.")).toBeNull()
  expect(screen.queryByRole("button", { name: "Create event" })).toBeNull()
  expect(screen.getByText(/don't create a duplicate/)).toBeTruthy()
})

it.each([
  ["proposed", "Ready to review"],
  ["approved", "Queued"],
  ["executing", "Creating event…"],
  ["outcome_unknown", "Waiting for confirmation"],
  ["failed", "Couldn't create event"],
  ["cancelled", "Cancelled"]
])(
  "renders %s from the action record instead of a prose completion claim",
  async (state, label) => {
    const action = { ...event, state, approval_available: state === "proposed" }
    vi.mocked(chrome.runtime.sendMessage).mockResolvedValue({
      ok: true,
      data: action
    })
    render(
      <TaskCard
        entry={{
          id: "booking",
          instruction: "book 2 pm tmrw for doctors appointment",
          message: "Done! Your event was created.",
          calendarActionId: action.action_id,
          calendarAction: action
        }}
        controller={{}}
      />
    )
    await screen.findByText(label, { exact: true })
    expect(screen.queryByText("Done! Your event was created.")).toBeNull()
    expect(screen.queryByText("Event created", { exact: true })).toBeNull()
    expect(
      Boolean(screen.queryByRole("button", { name: "Create event" }))
    ).toBe(state === "proposed")
    expect(
      vi
        .mocked(chrome.runtime.sendMessage)
        .mock.calls.every(([m]: any) => m.method === "GET")
    ).toBe(true)
  }
)

it("shows missing Calendar write consent as an actionable result without starting OAuth", async () => {
  const openCalendarSetup = vi.fn()
  render(
    <TaskCard
      entry={{
        id: "consent",
        instruction: "book 2 pm tmrw for doctors appointment",
        message:
          "Enable Calendar event creation, then I can prepare this event.",
        errorCode: "calendar_write_scope_required"
      }}
      controller={{ openCalendarSetup }}
    />
  )
  expect(
    screen.getByText(
      "Enable Calendar event creation, then I can prepare this event."
    )
  ).toBeTruthy()
  expect(screen.queryByLabelText("Calendar event")).toBeNull()
  fireEvent.click(screen.getByRole("button", { name: "Review calendars" }))
  expect(openCalendarSetup).toHaveBeenCalledOnce()
  expect(chrome.runtime.sendMessage).not.toHaveBeenCalled()
})
