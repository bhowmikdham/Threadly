import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import React from "react"
import { describe, expect, it, vi } from "vitest"

import { Booking } from "../components/Booking"
import { Settings } from "../components/Settings"
import { TaskCard } from "../components/TaskCard"

const artifact: any = {
  artifact_id: "schedule-1",
  task_id: "task-1",
  revision: 1,
  artifact: {
    kind: "schedule_options",
    content: {
      slot_request_id: "slots-1",
      expires_at: "2099-09-25T00:00:00Z",
      slots: [
        {
          id: "slot-1",
          start: "2099-09-24T03:00:00Z",
          end: "2099-09-24T03:30:00Z",
          timezone: "Australia/Melbourne"
        }
      ]
    }
  }
}
function fake(fn: (m: any) => any) {
  vi.mocked(chrome.runtime.sendMessage).mockImplementation(
    async (m: any) =>
      ({
        ok: true,
        data:
          m.type === "API"
            ? await fn(m)
            : m.type === "STATUS"
              ? {
                  origin: "http://127.0.0.1:8000",
                  redirectUri: "https://test.chromiumapp.org/oauth/callback"
                }
              : null
      }) as any
  )
}
describe("workflow continuation and scheduling", () => {
  it("submits a typed answer only for the fields the backend requested", () => {
    const answer = vi.fn()
    render(
      <TaskCard
        entry={
          {
            id: "entry",
            instruction: "Check 4 tomorrow",
            task: {
              task_id: "t",
              state: "needs_clarification",
              version: 3,
              question: {
                question_id: "q",
                expected_version: 3,
                fields: ["am_or_pm"],
                prompt: "Which time of day?"
              }
            }
          } as any
        }
        controller={{ answer, selection: null }}
      />
    )
    fireEvent.change(screen.getByLabelText("Time of day"), {
      target: { value: "PM" }
    })
    fireEvent.click(screen.getByText("Continue request"))
    expect(answer.mock.calls[0][1]).toEqual({ am_or_pm: "PM" })
    expect(screen.queryByText("date phrase")).toBeNull()
  })
  it("disables expired questions instead of replaying an old answer", () => {
    render(
      <TaskCard
        entry={
          {
            id: "entry",
            instruction: "Check time",
            task: {
              task_id: "t",
              state: "needs_clarification",
              version: 3,
              question: { fields: ["am_or_pm"], expired: true }
            }
          } as any
        }
        controller={{ answer: vi.fn(), selection: null }}
      />
    )
    expect(
      (screen.getByText("Continue request") as HTMLButtonElement).disabled
    ).toBe(true)
  })
  it("cancels the displayed task and never treats cancellation as a send", () => {
    const cancel = vi.fn()
    const entry: any = {
      id: "entry",
      instruction: "Summarise",
      task: { task_id: "t", state: "running", version: 7 }
    }
    render(<TaskCard entry={entry} controller={{ cancel }} />)
    fireEvent.click(screen.getByText("Cancel task"))
    expect(cancel).toHaveBeenCalledWith(entry)
  })
  it("requires rechecking the selected slot and exact event approval", async () => {
    const calls: any[] = []
    const event: any = {
      action_id: "event-1",
      state: "proposed",
      version: 4,
      payload_hash: "event-hash",
      approval_available: true,
      blockers: [],
      preview: {
        calendar_id: "primary",
        send_updates: "all",
        event: {
          summary: "Project meeting",
          location: "Online",
          attendees: [{ email: "alex@example.test" }],
          start: {
            dateTime: "2099-09-24T03:00:00Z",
            timeZone: "Australia/Melbourne"
          },
          end: { dateTime: "2099-09-24T03:30:00Z" }
        }
      }
    }
    fake((m) => {
      calls.push(m)
      if (m.path === "/assistant/tasks/task-1")
        return { context_snapshot_id: "ctx" }
      if (m.path === "/assistant/context-snapshots/ctx")
        return { thread_id: "thread-1", thread_version: 8 }
      if (m.path === "/calendar/negotiations") return { id: "n1", version: 1 }
      if (m.path.endsWith("/offers"))
        return { id: "offer-1", negotiation_id: "n1" }
      if (m.path === "/calendar/negotiations/n1")
        return { id: "n1", version: 2 }
      if (m.path.endsWith("/selections"))
        return {
          id: "selection-1",
          negotiation_id: "n1",
          state: "selected",
          usable: true
        }
      if (m.path === "/calendar/calendars")
        return {
          calendars: [
            { id: "primary", summary: "Personal", event_write_acl: true }
          ]
        }
      if (m.path === "/calendar/preferences")
        return { preferences: { calendar_ids: ["primary"] } }
      if (m.path.endsWith("/calendar-actions")) return event
      if (m.path.endsWith("/approve"))
        return { ...event, state: "approved", approval_available: false }
      return event
    })
    render(<Booking value={artifact} />)
    fireEvent.click(screen.getByText("Select and recheck"))
    await screen.findByText("Event details")
    await waitFor(() => expect(screen.getByText("Personal")).toBeTruthy())
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Project meeting" }
    })
    fireEvent.change(screen.getByLabelText("Calendar"), {
      target: { value: "primary" }
    })
    fireEvent.change(screen.getByLabelText("Attendees"), {
      target: { value: "alex@example.test" }
    })
    fireEvent.click(screen.getByText("Review event preview"))
    await screen.findByText("Approve and create event")
    expect(
      calls.find((c) => c.path.endsWith("/calendar-actions")).body
    ).toMatchObject({
      selection_id: "selection-1",
      expected_revision: 1,
      calendar_id: "primary",
      attendees: ["alex@example.test"],
      send_updates: "all"
    })
    expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(false)
    expect(
      (screen.getByText("Approve and create event") as HTMLButtonElement)
        .disabled
    ).toBe(true)
    fireEvent.click(screen.getByRole("checkbox"))
    fireEvent.click(screen.getByText("Approve and create event"))
    await waitFor(() =>
      expect(
        calls.find((c) => c.path.endsWith("/approve"))?.body
      ).toMatchObject({ payload_hash: "event-hash", expected_version: 4 })
    )
  })
  it("refuses to book an expired option", () => {
    fake(() => null)
    render(
      <Booking
        value={{
          ...artifact,
          artifact: {
            ...artifact.artifact,
            content: {
              ...artifact.artifact.content,
              expires_at: "2000-01-01T00:00:00Z"
            }
          }
        }}
      />
    )
    expect(
      (screen.getByText("Select and recheck") as HTMLButtonElement).disabled
    ).toBe(true)
  })
  it("shows an invalid server URL as a recoverable error", async () => {
    fake(() => null)
    render(
      <Settings
        user={null}
        capabilities={[]}
        onAuth={vi.fn()}
        onClose={vi.fn()}
        onPreferences={vi.fn()}
      />
    )
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Backend server") as HTMLInputElement).value
      ).toBe("http://127.0.0.1:8000")
    )
    fireEvent.change(screen.getByLabelText("Backend server"), {
      target: { value: "http://public.example.test" }
    })
    fireEvent.click(screen.getByText("Save server"))
    await screen.findByText("Use HTTPS, or a localhost tunnel for development.")
    expect(chrome.permissions.request).not.toHaveBeenCalled()
  })
})
