import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, expect, it, vi } from "vitest"

import { CalendarSetup } from "../components/CalendarSetup"
import { TaskCard } from "../components/TaskCard"
import { preferencesStatus } from "../lib/scheduling-readiness"

const capabilities = ["calendar_read", "calendar_list"].map((id) => ({
  id,
  ready: true,
  enabled: true,
  status: "ready"
}))
const preferences = {
  timezone: "Australia/Melbourne",
  calendar_ids: ["primary", "holiday"],
  working_periods: [{ weekday: 1, start_minute: 540, end_minute: 1020 }],
  buffer_before_minutes: 0,
  buffer_after_minutes: 0,
  minimum_notice_minutes: 60,
  default_duration_minutes: 30
}
let saved: any, calendars: any[], calls: any[], conflict: boolean
beforeEach(() => {
  saved = {
    version: 3,
    account_version: 2,
    needs_review: false,
    preferences: structuredClone(preferences)
  }
  calendars = [
    { id: "primary", summary: "Personal", can_read_busy: true },
    { id: "holiday", summary: "Holidays in India", can_read_busy: true }
  ]
  calls = []
  conflict = false
  vi.mocked(chrome.runtime.sendMessage).mockImplementation(
    async (message: any) => {
      calls.push(message)
      if (message.path === "/calendar/calendars")
        return { ok: true, data: { account_version: 2, calendars } } as any
      if (message.path === "/calendar/preferences") {
        if (message.method === "PUT") {
          if (conflict)
            return {
              ok: false,
              error: { code: "calendar_context_changed", message: "Changed" }
            } as any
          saved = {
            ...saved,
            needs_review: false,
            version: saved.version + 1,
            preferences: message.body.preferences
          }
        }
        return { ok: true, data: saved } as any
      }
      if (message.path === "/calendar/freebusy")
        return {
          ok: true,
          data: {
            coverage: saved.preferences.calendar_ids.includes("holiday")
              ? "unknown"
              : "complete",
            calendars: saved.preferences.calendar_ids.map(
              (calendar_id: string) => ({
                calendar_id,
                status: calendar_id === "holiday" ? "unknown" : "known"
              })
            )
          }
        } as any
      throw new Error(`Unexpected ${message.path}`)
    }
  )
})
const props = () => ({
  capabilities,
  preferencesState: "ready" as const,
  onAuth: vi.fn(),
  onPreferences: vi.fn()
})
it("loads automatically, identifies the failed calendar, and requires an explicit selection repair", async () => {
  render(<CalendarSetup {...props()} />)
  await screen.findByText("Some calendars couldn't be checked")
  expect(
    (screen.getByLabelText("Holidays in India") as HTMLInputElement).checked
  ).toBe(true)
  expect(calls.some((c) => c.method === "PUT")).toBe(false)
  fireEvent.click(screen.getByLabelText("Holidays in India"))
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }))
  await screen.findByText("All selected calendars checked")
  const put = calls.find((c) => c.method === "PUT")
  expect(put.body).toMatchObject({
    expected_version: 3,
    preferences: { calendar_ids: ["primary"] }
  })
  expect(
    calls.filter((c) => c.path === "/calendar/freebusy").at(-1).body
      .expected_preferences_version
  ).toBe(4)
})
it("lets the user remove inaccessible and disappeared selected calendars", async () => {
  calendars = [
    { id: "primary", summary: "Personal", can_read_busy: true },
    { id: "holiday", summary: "Holidays in India", can_read_busy: false }
  ]
  saved.preferences.calendar_ids.push("disappeared")
  render(<CalendarSetup {...props()} />)
  await screen.findByText("Some calendars couldn't be checked")
  for (const name of ["Holidays in India", "Previously selected calendar"]) {
    const input = screen.getByLabelText(name) as HTMLInputElement
    expect(input.disabled).toBe(false)
    fireEvent.click(input)
    expect(input.checked).toBe(false)
  }
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }))
  await screen.findByText("All selected calendars checked")
  expect(saved.preferences.calendar_ids).toEqual(["primary"])
})
it("requires review after reconnect and never checks stale preferences", async () => {
  saved.needs_review = true
  expect(preferencesStatus(saved)).toBe("stale")
  render(<CalendarSetup {...props()} />)
  await screen.findByText(/Your connection changed/)
  expect(calls.some((c) => c.path === "/calendar/freebusy")).toBe(false)
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }))
  await screen.findByText("Some calendars couldn't be checked")
  expect(saved.needs_review).toBe(false)
})
it("does not overwrite concurrent settings with a blind retry", async () => {
  render(<CalendarSetup {...props()} />)
  await screen.findByText("Some calendars couldn't be checked")
  fireEvent.click(screen.getByLabelText("Holidays in India"))
  conflict = true
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }))
  await screen.findByText(/Calendar settings changed elsewhere/)
  expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1)
  expect(
    (screen.getByRole("button", { name: "Save changes" }) as HTMLButtonElement)
      .disabled
  ).toBe(true)
  expect(screen.getByRole("button", { name: "Reload calendars" })).toBeTruthy()
})
it("ignores a slow load after the account screen is unmounted", async () => {
  let finish: (value: any) => void
  vi.mocked(chrome.runtime.sendMessage).mockImplementation(
    async (message: any) =>
      message.path === "/calendar/calendars"
        ? new Promise((resolve) => {
            finish = resolve
          })
        : ({ ok: true, data: saved } as any)
  )
  const p = props()
  const view = render(<CalendarSetup {...p} />)
  view.unmount()
  finish!({ ok: true, data: { calendars } })
  await waitFor(() => expect(p.onPreferences).not.toHaveBeenCalled())
})
it.each([
  "calendar_preferences_stale",
  "calendar_context_changed",
  "calendar_coverage_incomplete"
])("offers direct settings recovery for %s", (errorCode) => {
  const openCalendarSetup = vi.fn()
  render(
    <TaskCard
      entry={{
        id: "tuesday",
        instruction: "am i free on the tuesday",
        message: "Holidays in India couldn't be checked.",
        errorCode
      }}
      controller={{ openCalendarSetup }}
    />
  )
  fireEvent.click(screen.getByRole("button", { name: "Review calendars" }))
  expect(openCalendarSetup).toHaveBeenCalledOnce()
})
