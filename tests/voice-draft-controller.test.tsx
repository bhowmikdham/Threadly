import { fireEvent, render, screen } from "@testing-library/react"
import { beforeEach, expect, it, vi } from "vitest"

import { useAssistant } from "../lib/use-assistant"
import SidePanel from "../sidepanel"

vi.mock("../lib/use-assistant", () => ({ useAssistant: vi.fn() }))
vi.mock("../components/TaskCard", () => ({ TaskCard: () => null }))
vi.mock("../components/VoiceOrb", () => ({
  VoiceOrb: ({ revealChat, onClose }: any) => (
    <div>
      <output aria-label="Draft exposed">{String(revealChat)}</output>
      <button onClick={() => onClose()}>Close synthetic voice</button>
    </div>
  )
}))

let controller: any
const draft = (id: string) => ({
  id,
  instruction: "Draft a reply",
  emailDraft: {
    draft_id: id,
    recipient: "Alex",
    subject: "Re: Feedback",
    body: "Hi Alex"
  }
})

beforeEach(() => {
  controller = {
    entries: [],
    busy: false,
    restoring: false,
    restoreFailed: false,
    selection: null,
    selectActive: vi.fn(),
    followActive: vi.fn(),
    newChat: vi.fn().mockResolvedValue(undefined),
    setError: vi.fn(),
    capabilities: [],
    conversationId: "chat"
  }
  vi.mocked(useAssistant).mockImplementation(() => controller)
  vi.mocked(chrome.tabs.query).mockResolvedValue([])
  vi.mocked(chrome.runtime.sendMessage).mockImplementation(
    async (message: any) =>
      ({
        ok: true,
        data:
          message.type === "STATUS"
            ? {
                user: { id: 1, email: "owner@example.test", name: "Owner" },
                origin: "http://127.0.0.1:8000"
              }
            : { capabilities: [] }
      }) as any
  )
})

it("exposes an already pending entry when its draft finishes after voice reopens", async () => {
  controller.entries = [
    draft("older"),
    { id: "pending", instruction: "Draft a reply", pending: true }
  ]
  const view = render(<SidePanel />)
  fireEvent.click(
    await screen.findByRole("button", { name: "Talk to Threadly" })
  )
  expect(screen.getByLabelText("Draft exposed").textContent).toBe("false")
  fireEvent.click(screen.getByText("Close synthetic voice"))
  fireEvent.click(screen.getByRole("button", { name: "Talk to Threadly" }))
  controller.entries = [draft("older"), draft("pending")]
  view.rerender(<SidePanel />)
  expect(screen.getByLabelText("Draft exposed").textContent).toBe("true")
  controller.entries = [
    ...controller.entries,
    { id: "thanks", instruction: "Thanks", message: "You're welcome." }
  ]
  view.rerender(<SidePanel />)
  expect(screen.getByLabelText("Draft exposed").textContent).toBe("true")
})

it("ends voice immediately when the user switches to a new chat", async () => {
  controller.newChat.mockReturnValue(new Promise(() => {}))
  render(<SidePanel />)
  fireEvent.click(
    await screen.findByRole("button", { name: "Talk to Threadly" })
  )
  expect(screen.getByText("Close synthetic voice")).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: /^New chat$/ }))
  expect(controller.newChat).toHaveBeenCalledOnce()
  expect(screen.queryByText("Close synthetic voice")).toBeNull()
})
