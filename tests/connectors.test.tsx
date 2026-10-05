import {
  fireEvent,
  render,
  screen,
  waitFor,
  within
} from "@testing-library/react"
import { beforeEach, expect, it, vi } from "vitest"

import { ConnectorDetails, ConnectorList } from "../components/Connectors"
import type { Capability } from "../lib/types"

vi.mock("../components/CalendarSetup", () => ({
  CalendarSetup: () => <p>Calendar preferences</p>
}))
const caps: Capability[] = [
  { id: "gmail_read", ready: true, enabled: true, status: "ready" },
  { id: "gmail_send", ready: false, enabled: false, status: "disabled" },
  { id: "calendar_read", ready: true, enabled: true, status: "ready" },
  { id: "calendar_list", ready: true, enabled: true, status: "ready" },
  {
    id: "calendar_events_read",
    ready: false,
    enabled: true,
    status: "scope_missing"
  }
]
const auth = vi.fn().mockResolvedValue(undefined)
const props = {
  user: { id: 1, email: "test@example.test", name: null },
  capabilities: caps,
  preferencesState: "ready" as const,
  onAuth: auth,
  onPreferences: vi.fn(),
  onBack: vi.fn()
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(chrome.runtime.sendMessage).mockResolvedValue({
    ok: true,
    data: { connected: false }
  } as any)
})
it("makes both connected and disconnected rows manageable", () => {
  const select = vi.fn()
  render(<ConnectorList capabilities={caps} onSelect={select} />)
  fireEvent.click(screen.getByRole("button", { name: "Manage Gmail" }))
  fireEvent.click(
    screen.getByRole("button", { name: "Manage Google Calendar" })
  )
  expect(select.mock.calls).toEqual([["gmail"], ["calendar"]])
})
it("shows compact skills and puts Calendar preferences directly on the page", () => {
  render(<ConnectorDetails {...props} id="calendar" />)
  const skills = screen.getByRole("list", { name: "Skills" })
  expect(within(skills).getAllByRole("listitem")).toHaveLength(4)
  expect(screen.queryByText("Available")).toBeNull()
  expect(screen.getByText("Find availability").closest("li")!.title).toBe(
    "Available"
  )
  expect(screen.getByText("Review agenda").closest("li")!.title).toBe(
    "Permission needed"
  )
  expect(screen.queryByRole("tablist")).toBeNull()
  expect(screen.getByText("Calendar preferences")).toBeTruthy()
  fireEvent.click(screen.getByText("Account & permissions"))
  expect(
    screen.queryByRole("button", { name: "Allow booking after review" })
  ).toBeNull()
  fireEvent.click(screen.getByRole("button", { name: "Allow event details" }))
  expect(chrome.runtime.sendMessage).toHaveBeenCalledWith({
    channel: "threadly",
    type: "LOGIN",
    capabilities: ["calendar_events_read"]
  })
})
it("requires shared-account confirmation and preserves visible errors on disconnect failure", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false)
  render(<ConnectorDetails {...props} id="gmail" />)
  fireEvent.click(screen.getByText("Account & permissions"))
  const disconnect = screen.getByRole("button", {
    name: "Disconnect Google account"
  })
  fireEvent.click(disconnect)
  expect(chrome.runtime.sendMessage).not.toHaveBeenCalled()
  confirm.mockReturnValue(true)
  vi.mocked(chrome.runtime.sendMessage).mockResolvedValue({
    ok: false,
    error: { message: "Disconnect unavailable" }
  } as any)
  fireEvent.click(disconnect)
  await screen.findByText("Disconnect unavailable")
  expect(auth).not.toHaveBeenCalled()
  vi.mocked(chrome.runtime.sendMessage).mockResolvedValue({
    ok: true,
    data: { connected: false }
  } as any)
  fireEvent.click(disconnect)
  await waitFor(() => expect(auth).toHaveBeenCalledOnce())
  expect(chrome.runtime.sendMessage).toHaveBeenLastCalledWith({
    channel: "threadly",
    type: "DISCONNECT_GOOGLE"
  })
  confirm.mockRestore()
})
