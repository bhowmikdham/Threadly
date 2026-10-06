import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { TaskCard } from "../components/TaskCard"
import { useAssistant } from "../lib/use-assistant"

describe("conversational email drafting", () => {
  it("does not expose a planner rationale from an older unsupported task", () => {
    const diagnostic =
      "Email draft requested but recipient and content details are absent."
    render(
      <TaskCard
        entry={
          {
            id: "old-task",
            instruction: "Could you help me draft an email?",
            task: {
              task_id: "task",
              state: "unsupported",
              route: { decision: { rationale: diagnostic } }
            }
          } as any
        }
        controller={{}}
      />
    )
    expect(screen.queryByText(diagnostic)).toBeNull()
    expect(screen.getByText(/What would you like to do next/)).toBeTruthy()
  })

  it("keeps clarifications conversational and sends the follow-up in the same chat", async () => {
    const originalStorage = chrome.storage.session
    ;(chrome.storage as any).session = {
      get: vi.fn().mockResolvedValue({}),
      set: vi.fn().mockResolvedValue(undefined),
      remove: vi.fn()
    }
    const turns: any[] = []
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((async (
      message: any
    ) => {
      turns.push(message)
      return {
        ok: true,
        data: {
          conversation_id: message.body.conversation_id,
          version: message.body.expected_version + 1,
          kind: turns.length === 1 ? "clarification" : "message",
          text:
            turns.length === 1
              ? "Who’s it for, and what would you like to say?"
              : "Here’s a draft you can edit. Nothing has been sent.\n\nSubject: Thank you\n\nHi Alex,\n\nThanks for your help."
        }
      }
    }) as any)
    function Chat() {
      const controller = useAssistant({
        id: 1,
        email: "me@example.test",
        name: "Tester"
      })
      return (
        <>
          <button
            disabled={controller.restoring || controller.busy}
            onClick={() =>
              void controller.submit("Could you help me draft an email?")
            }>
            Draft email
          </button>
          <button
            disabled={controller.restoring || controller.busy}
            onClick={() =>
              void controller.submit("To Alex, thanking them for their help")
            }>
            Add details
          </button>
          {controller.entries.map((entry) => (
            <TaskCard key={entry.id} entry={entry} controller={controller} />
          ))}
        </>
      )
    }
    try {
      render(<Chat />)
      await waitFor(() =>
        expect(
          (screen.getByText("Draft email") as HTMLButtonElement).disabled
        ).toBe(false)
      )
      fireEvent.click(screen.getByText("Draft email"))
      await screen.findByText("Who’s it for, and what would you like to say?")
      expect(screen.queryByText("Let’s try another way")).toBeNull()
      expect(screen.queryByText("I’m preparing that for you.")).toBeNull()
      expect(screen.queryByRole("button", { name: /retry/i })).toBeNull()
      await waitFor(() =>
        expect(
          (screen.getByText("Add details") as HTMLButtonElement).disabled
        ).toBe(false)
      )
      fireEvent.click(screen.getByText("Add details"))
      await screen.findByText(/Subject: Thank you/)
      expect(turns).toHaveLength(2)
      expect(turns[1].body.conversation_id).toBe(turns[0].body.conversation_id)
      expect(turns[1].body.expected_version).toBe(1)
      expect(
        turns.every((turn) => turn.path === "/assistant/conversation-turns")
      ).toBe(true)
      expect(screen.queryByRole("button", { name: /^send/i })).toBeNull()
      expect(screen.queryByRole("button", { name: /approve/i })).toBeNull()
    } finally {
      ;(chrome.storage as any).session = originalStorage
    }
  })
})
