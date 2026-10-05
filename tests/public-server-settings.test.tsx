import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, expect, it, vi } from "vitest"

import { Settings } from "../components/Settings"

const publicOrigin = "https://threadly-api.example.test"

vi.mock("../lib/security", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/security")>()),
  DEFAULT_BACKEND: "https://threadly-api.example.test",
  publishedBackendBuild: true
}))
vi.mock("../components/CalendarSetup", () => ({ CalendarSetup: () => null }))

beforeEach(() => {
  vi.mocked(chrome.permissions.request).mockReset()
  vi.mocked(chrome.runtime.sendMessage).mockReset()
  vi.mocked(chrome.runtime.sendMessage).mockResolvedValue({
    ok: true,
    data: { origin: publicOrigin, redirectUri: "" }
  } as any)
})

it("re-grants the pinned server after browser permission is removed", async () => {
  const onAuth = vi.fn().mockResolvedValue(undefined)
  vi.mocked(chrome.permissions.request).mockImplementation(async () => true)
  render(
    <Settings
      user={{ id: 1, email: "test@example.test", name: null }}
      capabilities={[]}
      onAuth={onAuth}
      onClose={vi.fn()}
      onPreferences={vi.fn()}
    />
  )

  fireEvent.click(screen.getByText("Troubleshooting"))
  fireEvent.click(screen.getByRole("button", { name: "Reconnect server" }))
  await waitFor(() => expect(onAuth).toHaveBeenCalledOnce())
  expect(chrome.permissions.request).toHaveBeenCalledWith({
    origins: [`${publicOrigin}/*`]
  })
  expect(screen.queryByText("Backend server")).toBeNull()
})
