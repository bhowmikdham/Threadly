import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, expect, it, vi } from "vitest"

import { MeetingEmailEvent } from "../components/MeetingEmailEvent"

const source = {
  kind: "gmail_message" as const,
  thread_id: "abc123",
  message_id: "def456"
}
const draft = {
  source,
  context_snapshot_id: "capture",
  title: "Fresh meeting subject",
  source_subject: "Fresh meeting subject",
  source_sender: "sender@example.test",
  source_excerpt: "Please meet tomorrow. <script>do not execute</script>",
  source_truncated: false,
  timezone: "Australia/Melbourne",
  default_duration_minutes: 30,
  preferences_version: 2,
  calendars: [{ id: "work", name: "Work" }],
  confirmation_required: true
}
let calls: any[], current: any, failPreview: boolean, failEdit: boolean
let savedId: string | null
beforeEach(() => {
  calls = []
  current = null
  failPreview = false
  failEdit = false
  savedId = null
  vi.mocked(chrome.runtime.sendMessage).mockImplementation(
    async (message: any) => {
      if (message.type === "ACTION_REFERENCE") {
        if (message.actionId) savedId = message.actionId
        return { ok: true, data: message.actionId ? null : savedId }
      }
      calls.push(message)
      if (message.path.endsWith("/draft")) return { ok: true, data: draft }
      if (message.path.endsWith("/previews")) {
        if (failPreview) {
          failPreview = false
          return { ok: false, error: { message: "Connection lost" } }
        }
        current = {
          action_id: "action-" + calls.length,
          state: "proposed",
          version: 1,
          payload_hash: "exact-hash",
          approval_available: true,
          blockers: [],
          authorization: "separate_exact_event_approval",
          preview: {
            calendar_id: "work",
            calendar_name: "Work",
            send_updates: message.body.send_updates,
            event: {
              summary: message.body.title,
              start: {
                dateTime: "2030-10-09T03:00:00Z",
                timeZone: draft.timezone
              },
              end: {
                dateTime: "2030-10-09T03:30:00Z",
                timeZone: draft.timezone
              },
              location: "",
              description: "",
              attendees: message.body.attendees.map((email: string) => ({
                email
              }))
            }
          }
        }
      }
      if (message.path.endsWith("/approve"))
        current = {
          ...current,
          state: "succeeded",
          version: 2,
          approval_available: false
        }
      if (message.path.endsWith("/reject")) {
        if (failEdit)
          return {
            ok: false,
            error: { message: "Action changed; reload its current state." }
          }
        return { ok: true, data: { ...current, state: "rejected", version: 2 } }
      }
      return { ok: true, data: current }
    }
  )
})
const open = async () => {
  fireEvent.click(screen.getByRole("button", { name: "Create event" }))
  await screen.findByRole("form", { name: "Event from email" })
}
const fill = () => {
  fireEvent.change(screen.getByLabelText("Date (required)"), {
    target: { value: "2030-10-09" }
  })
  fireEvent.change(screen.getByLabelText("Start time (required)"), {
    target: { value: "14:00" }
  })
}
it("opens owned source details and requires fields plus a final exact confirmation", async () => {
  const { container } = render(<MeetingEmailEvent source={source} />)
  await open()
  expect(calls).toHaveLength(1)
  expect(calls[0].body).toEqual({ source })
  expect(container.querySelector("script")).toBeNull()
  expect(
    (screen.getByLabelText("Date (required)") as HTMLInputElement).value
  ).toBe("")
  expect(
    (screen.getByLabelText("Attendees (optional)") as HTMLInputElement).value
  ).toBe("")
  expect(
    (
      screen.getByRole("button", {
        name: "Review event preview"
      }) as HTMLButtonElement
    ).disabled
  ).toBe(true)
  fill()
  fireEvent.click(screen.getByRole("button", { name: "Review event preview" }))
  await screen.findByText("Ready to review")
  expect(calls.some((call) => call.path.endsWith("/approve"))).toBe(false)
  expect(screen.getByText(/including with Always allow enabled/)).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: "Create event" }))
  await screen.findByText("Event created")
  expect(
    calls.find((call) => call.path.endsWith("/approve")).body
  ).toMatchObject({ expected_version: 1, payload_hash: "exact-hash" })
  expect(screen.queryByRole("button", { name: "Edit details" })).toBeNull()
})
it("requires invitation selection, retires a preview before edits, and preserves exact typed addresses", async () => {
  render(<MeetingEmailEvent source={source} />)
  await open()
  fill()
  fireEvent.change(screen.getByLabelText("Attendees (optional)"), {
    target: { value: "guest@example.test" }
  })
  expect(
    (
      screen.getByRole("button", {
        name: "Review event preview"
      }) as HTMLButtonElement
    ).disabled
  ).toBe(true)
  fireEvent.click(screen.getByRole("checkbox"))
  fireEvent.click(screen.getByRole("button", { name: "Review event preview" }))
  await screen.findByText("Ready to review")
  expect(
    calls.find((call) => call.path.endsWith("/previews")).body
  ).toMatchObject({ attendees: ["guest@example.test"], send_updates: "all" })
  fireEvent.click(screen.getByRole("button", { name: "Edit details" }))
  await screen.findByRole("form", { name: "Event from email" })
  expect(
    calls.find((call) => call.path.endsWith("/reject")).body.expected_version
  ).toBe(1)
  fireEvent.change(screen.getByLabelText("Title"), {
    target: { value: "Edited meeting" }
  })
  fireEvent.click(screen.getByRole("button", { name: "Review event preview" }))
  await screen.findByRole("heading", { name: "Edited meeting" })
  expect(calls.some((call) => call.path.endsWith("/approve"))).toBe(false)
})
it("keeps the idempotency key after a lost preview response and refuses raced edits", async () => {
  render(<MeetingEmailEvent source={source} />)
  await open()
  fill()
  failPreview = true
  fireEvent.click(screen.getByRole("button", { name: "Review event preview" }))
  await screen.findByRole("alert")
  fireEvent.click(screen.getByRole("button", { name: "Review event preview" }))
  await screen.findByText("Ready to review")
  const previews = calls.filter((call) => call.path.endsWith("/previews"))
  expect(previews[0].body.request_id).toBe(previews[1].body.request_id)
  failEdit = true
  fireEvent.click(screen.getByRole("button", { name: "Edit details" }))
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain("Action changed")
  )
  expect(screen.queryByRole("form", { name: "Event from email" })).toBeNull()
})
it("does not read or prepare an event while context is locked", () => {
  render(<MeetingEmailEvent source={source} disabled />)
  fireEvent.click(screen.getByRole("button", { name: "Create event" }))
  expect(calls).toHaveLength(0)
})

it("reopens the saved event status without preparing another candidate", async () => {
  const first = render(<MeetingEmailEvent source={source} />)
  await open()
  fill()
  fireEvent.click(screen.getByRole("button", { name: "Review event preview" }))
  await screen.findByText("Ready to review")
  fireEvent.click(screen.getByRole("button", { name: "Create event" }))
  await screen.findByText("Event created")
  first.unmount()
  render(<MeetingEmailEvent source={source} />)
  fireEvent.click(screen.getByRole("button", { name: "Create event" }))
  await screen.findByText("Event created")
  expect(calls.filter((call) => call.path.endsWith("/draft"))).toHaveLength(1)
  expect(calls.filter((call) => call.path.endsWith("/previews"))).toHaveLength(
    1
  )
  expect(calls.filter((call) => call.path.endsWith("/approve"))).toHaveLength(1)
})
