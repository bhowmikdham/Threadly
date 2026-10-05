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
it("explains feature availability in text and keeps Calendar preferences on the page", () => {
  render(<ConnectorDetails {...props} id="calendar" />)
  fireEvent.click(screen.getByText("What Threadly can do"))
  const skills = screen.getByRole("list", { name: "Skills" })
  expect(within(skills).getAllByRole("listitem")).toHaveLength(4)
  expect(
    within(screen.getByText("Find availability").closest("li")!).getByText(
      "Available"
    )
  ).toBeTruthy()
  expect(
    within(screen.getByText("Review agenda").closest("li")!).getByText(
      "Permission needed"
    )
  ).toBeTruthy()
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
it("keeps reconnect visible, disables repeated requests, and reports errors without changing scopes", async () => {
  let finish!: (value: any) => void
  vi.mocked(chrome.runtime.sendMessage).mockReturnValue(
    new Promise((resolve) => {
      finish = resolve
    }) as any
  )
  render(<ConnectorDetails {...props} id="gmail" />)
  const reconnect = screen.getByRole("button", {
    name: "Reconnect Gmail"
  }) as HTMLButtonElement
  expect(reconnect.closest("details")).toBeNull()
  fireEvent.click(reconnect)
  expect(reconnect.disabled).toBe(true)
  expect(screen.getByRole("status").textContent).toBe("Updating connection…")
  expect(chrome.runtime.sendMessage).toHaveBeenCalledWith({
    channel: "threadly",
    type: "LOGIN",
    capabilities: ["gmail_read"]
  })
  finish({ ok: false, error: { message: "Connection cancelled. Try again." } })
  await screen.findByText("Connection cancelled. Try again.")
  expect(reconnect.disabled).toBe(false)
  expect(auth).not.toHaveBeenCalled()
})
it("keeps Gmail sending and Calendar event creation consent explicit", async () => {
  const { rerender } = render(
    <ConnectorDetails
      {...props}
      id="calendar"
      capabilities={[
        ...caps,
        {
          id: "calendar_write",
          ready: false,
          enabled: true,
          status: "scope_missing"
        }
      ]}
    />
  )
  fireEvent.click(screen.getByRole("button", { name: "Enable event creation" }))
  await waitFor(() => expect(auth).toHaveBeenCalledOnce())
  expect(chrome.runtime.sendMessage).toHaveBeenLastCalledWith({
    channel: "threadly",
    type: "LOGIN",
    capabilities: ["calendar_write"]
  })
  rerender(
    <ConnectorDetails
      {...props}
      id="gmail"
      capabilities={caps.map((c) =>
        c.id === "gmail_send" ? { ...c, enabled: true } : c
      )}
    />
  )
  fireEvent.click(screen.getByText("Account & permissions"))
  fireEvent.click(
    screen.getByRole("button", { name: "Allow sending after review" })
  )
  await waitFor(() => expect(auth).toHaveBeenCalledTimes(2))
  expect(chrome.runtime.sendMessage).toHaveBeenLastCalledWith({
    channel: "threadly",
    type: "LOGIN",
    capabilities: ["gmail_send"]
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
