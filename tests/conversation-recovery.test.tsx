import { act, renderHook, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { useAssistant } from "../lib/use-assistant"

const user = { id: 1, email: "me@example.test", name: "Tester" }
const conversationId = "00000000-0000-4000-8000-000000000001"
const pendingId = "00000000-0000-4000-8000-000000000002"
const path = `/assistant/conversations/${conversationId}/recover`
const failed = (code: string, message = "Keep this work for review.") => ({
  ok: false,
  error: { code, message, status: 409 }
})
const recovered = (extra: any = {}) => ({
  ok: true,
  data: {
    conversation_id: conversationId,
    recovered_request_id: pendingId,
    version: 1,
    kind: "message",
    text: "The unfinished request was cancelled. You can continue this chat.",
    error_code: "conversation_request_cancelled",
    ...extra
  }
})
beforeEach(() => {
  ;(chrome.storage as any).session = {
    get: vi.fn().mockResolvedValue({
      "threadlyConversation:1": { id: conversationId, version: 0 }
    }),
    set: vi.fn().mockResolvedValue(undefined),
    remove: vi.fn().mockResolvedValue(undefined)
  }
})
async function stranded(handler: (m: any) => any) {
  const calls: any[] = []
  vi.mocked(chrome.runtime.sendMessage).mockImplementation((async (m: any) => {
    calls.push(m)
    if (m.path === `/assistant/conversations/${conversationId}`)
      return {
        ok: true,
        data: {
          conversation_id: conversationId,
          version: 0,
          history: [],
          pending_request_id: pendingId
        }
      }
    if (m.path === "/assistant/conversation-turns")
      return {
        ok: true,
        data: {
          conversation_id: conversationId,
          version: m.body.expected_version + 1,
          text: "Here is the availability at 2 pm."
        }
      }
    return handler(m)
  }) as any)
  const hook = renderHook(() => useAssistant(user))
  await waitFor(() => expect(hook.result.current.restoring).toBe(false))
  expect(hook.result.current.recovery?.requestId).toBe(pendingId)
  return { ...hook, calls }
}

describe("guarded recovery of an older unfinished conversation", () => {
  it("restores remaining calendar choices from a recovered clarification", async () => {
    const choices = {
      expires_at: new Date(Date.now() + 60_000).toISOString(),
      choices: [
        {
          choice_id: "choice-1",
          label: "Personal calendar",
          access: "editable"
        }
      ]
    }
    const { result, calls } = await stranded(() =>
      recovered({
        kind: "clarification",
        text: "Choose a calendar.",
        error_code: undefined,
        calendar_choices: choices
      })
    )
    await act(async () => {
      await result.current.recoverConversation("recover")
    })
    expect(result.current.entries[0]).toMatchObject({
      instruction: "",
      calendarChoices: choices
    })
    expect(result.current.canChooseCalendar(result.current.entries[0])).toBe(
      true
    )
    expect(calls).toHaveLength(2)
  })
  it("recovers an owned typed event without inventing an instruction or approving it", async () => {
    const { result, calls } = await stranded(() =>
      recovered({
        kind: "calendar_event",
        text: "Review this event before creating it.",
        error_code: undefined,
        calendar_action_id: "owned-event",
        calendar_action: { action_id: "owned-event", state: "proposed" }
      })
    )
    expect(result.current.contextLocked).toBe(true)
    await act(async () => {
      await result.current.submit("am i free at 2 pm tmrw?")
      await result.current.recoverConversation("cancel")
    })
    expect(calls).toHaveLength(1)
    await act(async () => {
      await result.current.recoverConversation("recover")
    })
    expect(calls[1]).toMatchObject({
      path,
      body: {
        pending_request_id: pendingId,
        expected_version: 0,
        operation: "recover"
      }
    })
    expect(result.current.entries).toHaveLength(1)
    expect(result.current.entries[0]).toMatchObject({
      instruction: "",
      calendarActionId: "owned-event",
      calendarAction: { state: "proposed" }
    })
    expect(result.current.recovery).toBeNull()
    expect(result.current.contextLocked).toBe(false)
    expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(false)
    await act(async () => {
      await result.current.submit("am i free at 2 pm tmrw?")
    })
    expect(calls[2].body).toMatchObject({
      conversation_id: conversationId,
      expected_version: 1,
      instruction: "am i free at 2 pm tmrw?"
    })
  })

  it("requires a separate explicit cancellation after the server reports no saved result", async () => {
    const { result, calls } = await stranded((m) =>
      m.body.operation === "recover"
        ? failed("conversation_result_unavailable")
        : recovered()
    )
    await act(async () => {
      await result.current.recoverConversation("recover")
    })
    expect(result.current.recovery?.canCancel).toBe(true)
    expect(result.current.contextLocked).toBe(true)
    expect(calls.filter((c) => c.path === path)).toHaveLength(1)
    await act(async () => {
      await result.current.recoverConversation("cancel")
    })
    expect(calls[2].body).toEqual({
      pending_request_id: pendingId,
      expected_version: 0,
      operation: "cancel"
    })
    expect(result.current.entries[0].errorCode).toBe(
      "conversation_request_cancelled"
    )
    expect(result.current.recovery).toBeNull()
    expect(calls.some((c) => c.path === "/assistant/conversation-turns")).toBe(
      false
    )
  })

  it.each([
    "conversation_busy",
    "conversation_work_exists",
    "conversation_pending_changed",
    "conversation_version_conflict",
    "conversation_account_changed",
    "conversation_not_found"
  ])("keeps %s fenced and never sends a replacement turn", async (code) => {
    const { result, calls } = await stranded(() => failed(code))
    await act(async () => {
      await result.current.recoverConversation("recover")
    })
    expect(result.current.recovery?.message).toBe("Keep this work for review.")
    expect(result.current.recovery?.canCancel).toBe(false)
    expect(result.current.contextLocked).toBe(true)
    await act(async () => {
      await result.current.submit("am i free at 2 pm tmrw?")
    })
    expect(calls).toHaveLength(2)
    expect(result.current.entries).toHaveLength(0)
  })

  it("removes cancellation when work was saved concurrently and recovers the existing result", async () => {
    let attempts = 0
    const { result, calls } = await stranded((m) => {
      if (++attempts === 1) return failed("conversation_result_unavailable")
      if (m.body.operation === "cancel")
        return failed("conversation_result_available")
      return recovered({
        text: "An existing task is ready.",
        task: { task_id: "task-1", state: "succeeded" }
      })
    })
    await act(async () => {
      await result.current.recoverConversation("recover")
    })
    await act(async () => {
      await result.current.recoverConversation("cancel")
    })
    expect(result.current.recovery?.canCancel).toBe(false)
    await act(async () => {
      await result.current.recoverConversation("cancel")
    })
    expect(calls).toHaveLength(3)
    await act(async () => {
      await result.current.recoverConversation("recover")
    })
    expect(result.current.entries[0].task.task_id).toBe("task-1")
    expect(
      calls.filter((c) => c.path === path).map((c) => c.body.operation)
    ).toEqual(["recover", "cancel", "recover"])
  })

  it("replays the same cancellation after a dropped response without creating new work", async () => {
    let cancellations = 0
    const { result, calls } = await stranded((m) => {
      if (m.body.operation === "recover")
        return failed("conversation_result_unavailable")
      if (++cancellations === 1) throw new Error("Connection interrupted")
      return recovered()
    })
    await act(async () => {
      await result.current.recoverConversation("recover")
    })
    await act(async () => {
      await result.current.recoverConversation("cancel")
    })
    expect(result.current.recovery?.canCancel).toBe(true)
    await act(async () => {
      await result.current.recoverConversation("cancel")
    })
    expect(calls[3].body).toEqual(calls[2].body)
    expect(result.current.entries).toHaveLength(1)
    expect(result.current.recovery).toBeNull()
  })

  it.each([
    { conversation_id: "another-chat" },
    { recovered_request_id: "another-request" },
    { version: 99 }
  ])(
    "rejects a recovery response with inconsistent ownership/version %j",
    async (extra) => {
      const { result } = await stranded(() => recovered(extra))
      await act(async () => {
        await result.current.recoverConversation("recover")
      })
      expect(result.current.contextLocked).toBe(true)
      expect(result.current.recovery?.message).toContain("could not be matched")
      expect(result.current.entries).toHaveLength(0)
    }
  )
})
