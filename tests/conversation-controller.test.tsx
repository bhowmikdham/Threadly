import { act, renderHook, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { useAssistant } from "../lib/use-assistant"

const user = { id: 1, email: "me@example.test", name: "Tester" }
function mock(handler: (m: any) => any) {
  const calls: any[] = []
  vi.mocked(chrome.runtime.sendMessage).mockImplementation((async (m: any) => {
    calls.push(m)
    return { ok: true, data: await handler(m) }
  }) as any)
  return calls
}
const respond = (m: any, value: any = {}) => ({
  conversation_id: m.body.conversation_id,
  version: m.body.expected_version + 1,
  kind: "message",
  text: "Ready to help.",
  ...value
})

beforeEach(() => {
  ;(chrome.storage as any).session = {
    get: vi.fn().mockResolvedValue({}),
    set: vi.fn().mockResolvedValue(undefined),
    remove: vi.fn()
  }
})
describe("backend-owned conversation", () => {
  it("passes the browser IANA timezone unchanged for mail search and pagination", async () => {
    const original = Intl.DateTimeFormat.prototype.resolvedOptions
    const zone = vi
      .spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions")
      .mockImplementation(function () {
        return { ...original.call(this), timeZone: "Australia/Melbourne" }
      })
    try {
      const calls = mock((m) =>
        respond(m, {
          search: { filters: {}, results: [], next_cursor: "next" }
        })
      )
      const { result } = renderHook(() => useAssistant(user))
      await act(async () => {
        await result.current.submit("Check my latest inbox mail")
      })
      await act(async () => {
        await result.current.moreEmails(result.current.entries[0])
      })
      expect(calls).toHaveLength(2)
      expect(calls.map((call) => call.body.timezone)).toEqual([
        "Australia/Melbourne",
        "Australia/Melbourne"
      ])
    } finally {
      zone.mockRestore()
    }
  })
  it("sends informal turns unchanged in one versioned conversation without client intent rules", async () => {
    const calls = mock((m) => respond(m))
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.submit("hey")
    })
    await act(async () => {
      await result.current.submit("how are yo u")
    })
    expect(calls.map((c) => c.path)).toEqual([
      "/assistant/conversation-turns",
      "/assistant/conversation-turns"
    ])
    expect(calls[1].body).toMatchObject({
      conversation_id: calls[0].body.conversation_id,
      expected_version: 1,
      instruction: "how are yo u"
    })
    expect(calls[1].body.intent_hint).toBeUndefined()
    expect(result.current.entries).toHaveLength(2)
  })
  it("finds mail without selected source and shows cards", async () => {
    const calls = mock((m) =>
      respond(m, {
        search: {
          results: [{ message_id: "m1", thread_id: "t1" }],
          filters: { query: "GYG" },
          next_cursor: "next"
        }
      })
    )
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.submit("can you fetch me latest GYG order")
    })
    expect(calls).toHaveLength(1)
    expect(result.current.entries[0].inbox.results[0].message_id).toBe("m1")
    expect(result.current.error).toBe("")
  })
  it("lets backend interpret you tell me and later recipient answers using active task", async () => {
    const calls = mock((m) =>
      respond(m, {
        task: {
          task_id: "t",
          state: "needs_clarification",
          question: { fields: ["recipients"] }
        }
      })
    )
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.submit("draft a reply")
    })
    await act(async () => {
      await result.current.submit("you tell me")
    })
    expect(calls[1].body.active_task_id).toBe("t")
    expect(calls[1].body.instruction).toBe("you tell me")
    expect(calls.some((c) => c.path.endsWith("/inputs"))).toBe(false)
  })
  it("does not auto-confirm a returned proposal", async () => {
    const calls = mock((m) =>
      respond(m, {
        kind: "proposal",
        proposal: { plan_id: "p", state: "proposed" }
      })
    )
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.submit("Summarise, find slots and draft a reply")
    })
    expect(result.current.entries[0].proposal.plan_id).toBe("p")
    expect(calls.some((c) => c.path.endsWith("/confirm"))).toBe(false)
  })
  it("retries the exact failed request and prevents a second conflicting turn", async () => {
    let fail = true
    const calls = mock((m) => {
      if (fail) throw new Error("Interrupted")
      return respond(m)
    })
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.submit("Find GYG")
    })
    await act(async () => {
      await result.current.submit("another question")
    })
    expect(calls).toHaveLength(1)
    fail = false
    await act(async () => {
      await result.current.retry(result.current.entries[0])
    })
    expect(calls[1].body).toEqual(calls[0].body)
    expect(result.current.entries[0].error).toBeUndefined()
    const stored = (chrome.storage.session.set as any).mock.calls
    expect(
      stored.some(([value]) => value["threadlyConversation:1"].pendingTurn)
    ).toBe(true)
    expect(stored.at(-1)[0]["threadlyConversation:1"].pendingTurn).toBeNull()
  })
  it("preserves a tool-limited booking for exact retry and then permits an availability question", async () => {
    const calls: any[] = []
    let limited = true
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((async (
      m: any
    ) => {
      calls.push(m)
      if (limited)
        return {
          ok: false,
          error: {
            code: "conversation_tool_limit",
            message:
              "I reached the limit for this request. Try a smaller question.",
            status: 422
          }
        }
      return {
        ok: true,
        data: respond(m, {
          text:
            m.body.expected_version === 0
              ? "I couldn't prepare this event yet. I haven't confirmed a booking."
              : "That time is available.",
          ...(m.body.expected_version === 0
            ? { error_code: "calendar_event_not_prepared" }
            : {})
        })
      }
    }) as any)
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.submit("book 2 pm tmrw for doctors appointment")
    })
    const issued = { ...calls[0].body }
    expect(result.current.canRetry(result.current.entries[0])).toBe(true)
    expect(result.current.entries[0].error).toContain("same details")
    expect(result.current.entries[0].error).not.toMatch(
      /smaller|new conversation/
    )
    expect(result.current.contextLocked).toBe(true)
    await act(async () => {
      await result.current.submit("am i free at 2 pm tmrw?")
    })
    expect(calls).toHaveLength(1)
    limited = false
    await act(async () => {
      await result.current.retry(result.current.entries[0])
    })
    expect(calls[1].body).toEqual(issued)
    expect(result.current.error).toBe("")
    expect(result.current.entries[0].errorCode).toBe(
      "calendar_event_not_prepared"
    )
    expect(result.current.entries[0].calendarActionId).toBeUndefined()
    expect(result.current.canRetry(result.current.entries[0])).toBe(false)
    expect(result.current.contextLocked).toBe(false)
    await act(async () => {
      await result.current.submit("am i free at 2 pm tmrw?")
    })
    expect(calls[2].body).toMatchObject({
      conversation_id: issued.conversation_id,
      expected_version: 1,
      instruction: "am i free at 2 pm tmrw?",
      timezone: issued.timezone
    })
    expect(calls[2].body.request_id).not.toBe(issued.request_id)
    expect(result.current.entries).toHaveLength(2)
    expect(result.current.entries[1].message).toBe("That time is available.")
    expect(calls.every((c) => c.path === "/assistant/conversation-turns")).toBe(
      true
    )
  })
  it("does not repeatedly replay a new question when the server owns an older unfinished turn", async () => {
    const calls: any[] = []
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((async (
      m: any
    ) => {
      calls.push(m)
      if (m.path === "/assistant/conversation-turns")
        return {
          ok: false,
          error: {
            code: "conversation_retry_required",
            message: "Retry the unfinished message first.",
            status: 409
          }
        }
      return {
        ok: true,
        data: {
          conversation_id: calls[0].body.conversation_id,
          version: 0,
          history: [],
          pending_request_id: "older-booking"
        }
      }
    }) as any)
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.submit("am i free at 2 pm tmrw?")
    })
    expect(calls).toHaveLength(2)
    expect(calls[1].path).toBe(
      `/assistant/conversations/${calls[0].body.conversation_id}`
    )
    expect(result.current.canRetry(result.current.entries[0])).toBe(false)
    expect(result.current.entries[0].error).toContain(
      "Another unfinished message"
    )
    await act(async () => {
      await result.current.retry(result.current.entries[0])
    })
    expect(calls).toHaveLength(2)
    const stored = (chrome.storage.session.set as any).mock.calls
    expect(stored.at(-1)[0]["threadlyConversation:1"].pendingTurn).toBeNull()
  })
  it("does not change email context while an unfinished turn needs retry", async () => {
    const key = "threadlyConversation:1"
    const saved: Record<string, any> = {}
    ;(chrome.storage.session.get as any).mockImplementation(
      async (name: string) => (name in saved ? { [name]: saved[name] } : {})
    )
    ;(chrome.storage.session.set as any).mockImplementation(
      async (values: Record<string, any>) => {
        Object.assign(saved, values)
      }
    )
    ;(chrome.storage.session.remove as any).mockImplementation(
      async (name: string) => {
        delete saved[name]
      }
    )
    const calls = mock((m) => {
      if (m.path === "/threads/thread-1")
        return {
          thread: { thread_id: "thread-1", version: 1, subject: "Receipt" },
          messages: [{ gmail_msg_id: "message-1" }]
        }
      if (m.path === "/assistant/context-snapshots")
        return { context_snapshot_id: "ctx-1" }
      if (m.path === "/assistant/conversation-turns")
        throw new Error("Interrupted")
      return respond(m)
    })
    const selection = {
      thread: { thread_id: "thread-1", version: 1, subject: "Receipt" },
      messages: [{ gmail_msg_id: "message-1" }],
      selectedIds: ["message-1"],
      targetId: "message-1"
    } as any
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      result.current.setSelection(selection)
    })
    await act(async () => {
      await result.current.submit("Summarise this receipt")
    })
    const issued = calls.find(
      (call) => call.path === "/assistant/conversation-turns"
    )?.body
    expect(result.current.canRetry(result.current.entries[0])).toBe(true)
    const replacement = {
      thread: { thread_id: "thread-2", version: 1, subject: "Other receipt" },
      messages: [{ gmail_msg_id: "message-2" }],
      selectedIds: ["message-2"],
      targetId: "message-2"
    } as any
    await act(async () => {
      result.current.clearContext()
      result.current.setSelection(replacement)
      await result.current.chooseEmail({
        thread_id: "thread-2",
        message_id: "message-2"
      } as any)
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(result.current.selection?.thread.thread_id).toBe("thread-1")
    expect(result.current.contextLocked).toBe(true)
    expect(result.current.error).toMatch(/retry the unfinished message/i)
    expect(calls.some((call) => call.path === "/threads/thread-2")).toBe(false)
    expect(saved[key]?.pendingTurn?.body).toEqual(issued)
  })
  it("keeps an exact replay after a version conflict confirms the same request", async () => {
    const calls: any[] = []
    let failed = false
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((async (
      m: any
    ) => {
      calls.push(m)
      if (m.path === "/assistant/conversation-turns" && !failed) {
        failed = true
        return {
          ok: false,
          error: {
            code: "conversation_version_conflict",
            message: "Conversation changed",
            status: 409
          }
        }
      }
      if (m.path.startsWith("/assistant/conversations/"))
        return {
          ok: true,
          data: {
            conversation_id: calls[0].body.conversation_id,
            version: 1,
            history: [
              {
                user: "Hello",
                assistant: "Hi",
                request_id: calls[0].body.request_id
              }
            ],
            pending_request_id: null
          }
        }
      return { ok: true, data: respond(m, { text: "Hi" }) }
    }) as any)
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.submit("Hello")
    })
    expect(result.current.canRetry(result.current.entries[0])).toBe(true)
    await act(async () => {
      await result.current.retry(result.current.entries[0])
    })
    expect(calls[2].body).toEqual(calls[0].body)
    expect(result.current.entries[0].message).toBe("Hi")
    await act(async () => {
      await result.current.submit("Next")
    })
    expect(calls.at(-1).body.expected_version).toBe(1)
  })
  it("removes retry when the account changed and the original request cannot replay", async () => {
    vi.mocked(chrome.runtime.sendMessage).mockResolvedValue({
      ok: false,
      error: {
        code: "conversation_account_changed",
        message: "Account changed",
        status: 409
      }
    })
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.submit("Hello")
    })
    expect(result.current.entries[0].error).toContain(
      "Start a new conversation"
    )
    expect(result.current.canRetry(result.current.entries[0])).toBe(false)
  })
  it("restores the exact interrupted turn so a closed panel cannot orphan it", async () => {
    const body = {
      conversation_id: "123e4567-e89b-42d3-a456-426614174000",
      request_id: "123e4567-e89b-42d3-a456-426614174001",
      expected_version: 0,
      instruction: "Find GYG",
      timezone: "Australia/Melbourne",
      active_task_id: null
    }
    ;(chrome.storage.session.get as any).mockResolvedValue({
      "threadlyConversation:1": {
        id: body.conversation_id,
        version: 0,
        pendingTurn: { id: body.request_id, body }
      }
    })
    const calls = mock((m) =>
      m.path.startsWith("/assistant/conversations/")
        ? {
            conversation_id: body.conversation_id,
            version: 0,
            history: [],
            pending_request_id: body.request_id,
            context_snapshot_id: null,
            active_task_id: null
          }
        : respond(m)
    )
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(result.current.entries[0].instruction).toBe("Find GYG")
    await act(async () => {
      await result.current.retry(result.current.entries[0])
    })
    expect(calls.at(-1).body).toEqual(body)
  })
  it("keeps a pending replacement email through two panel reopens and retries the exact turn", async () => {
    const key = "threadlyConversation:1"
    const body = {
      conversation_id: "123e4567-e89b-42d3-a456-426614174000",
      request_id: "123e4567-e89b-42d3-a456-426614174001",
      expected_version: 1,
      instruction: "Summarise this newer receipt",
      timezone: "Australia/Melbourne",
      context_snapshot_id: "ctx-new",
      active_task_id: null
    }
    const saved: Record<string, any> = {
      [key]: {
        id: body.conversation_id,
        version: 1,
        pendingTurn: { id: body.request_id, body },
        selectionOverride: {
          threadId: "new-thread",
          threadVersion: 3,
          visibleMessageIds: ["new-message"],
          selectedIds: ["new-message"],
          targetId: "new-message"
        }
      }
    }
    ;(chrome.storage.session.get as any).mockImplementation(
      async (name: string) => (name in saved ? { [name]: saved[name] } : {})
    )
    ;(chrome.storage.session.set as any).mockImplementation(
      async (values: Record<string, any>) => {
        Object.assign(saved, values)
      }
    )
    ;(chrome.storage.session.remove as any).mockImplementation(
      async (name: string) => {
        delete saved[name]
      }
    )
    const calls = mock((m) => {
      if (m.path === `/assistant/conversations/${body.conversation_id}`)
        return {
          conversation_id: body.conversation_id,
          version: 1,
          history: [{ user: "Earlier request", assistant: "Earlier answer" }],
          pending_request_id: body.request_id,
          context_snapshot_id: "ctx-old"
        }
      if (m.path === "/threads/new-thread")
        return {
          thread: {
            thread_id: "new-thread",
            version: 3,
            subject: "New receipt"
          },
          messages: [{ gmail_msg_id: "new-message" }]
        }
      return respond(m)
    })
    const first = renderHook(() => useAssistant(user))
    await waitFor(() =>
      expect(first.result.current.selection?.thread.subject).toBe("New receipt")
    )
    expect(
      first.result.current.canRetry(first.result.current.entries.at(-1)!)
    ).toBe(true)
    first.unmount()

    const second = renderHook(() => useAssistant(user))
    await waitFor(() =>
      expect(second.result.current.selection?.thread.subject).toBe(
        "New receipt"
      )
    )
    expect(
      second.result.current.canRetry(second.result.current.entries.at(-1)!)
    ).toBe(true)
    await act(async () => {
      await second.result.current.retry(second.result.current.entries.at(-1)!)
    })
    const retried = calls.filter(
      (call) => call.path === "/assistant/conversation-turns"
    )
    expect(retried).toHaveLength(1)
    expect(retried[0].body).toEqual(body)
    second.unmount()
  })
  it("does not show an older server email for a legacy pending turn with a different source", async () => {
    const body = {
      conversation_id: "123e4567-e89b-42d3-a456-426614174000",
      request_id: "123e4567-e89b-42d3-a456-426614174001",
      expected_version: 1,
      instruction: "Summarise the newer receipt",
      timezone: "Australia/Melbourne",
      context_snapshot_id: "ctx-new",
      active_task_id: null
    }
    ;(chrome.storage.session.get as any).mockResolvedValue({
      "threadlyConversation:1": {
        id: body.conversation_id,
        version: 1,
        pendingTurn: { id: body.request_id, body }
      }
    })
    const calls = mock((m) => {
      if (m.path === `/assistant/conversations/${body.conversation_id}`)
        return {
          conversation_id: body.conversation_id,
          version: 1,
          history: [{ user: "Earlier request", assistant: "Earlier answer" }],
          pending_request_id: body.request_id,
          context_snapshot_id: "ctx-old"
        }
      if (m.path === "/assistant/context-snapshots/ctx-old")
        return {
          thread_id: "old-thread",
          thread_version: 1,
          ui_map: {
            visible_message_ids: ["old-message"],
            selected_message_ids: ["old-message"]
          }
        }
      if (m.path === "/threads/old-thread")
        return {
          thread: {
            thread_id: "old-thread",
            version: 1,
            subject: "Old receipt"
          },
          messages: [{ gmail_msg_id: "old-message" }]
        }
      return respond(m)
    })
    const { result } = renderHook(() => useAssistant(user))
    await waitFor(() =>
      expect(result.current.canRetry(result.current.entries.at(-1)!)).toBe(true)
    )
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(result.current.selection).toBeNull()
    expect(result.current.pendingUsesHiddenEmail).toBe(true)
    await act(async () => {
      await result.current.retry(result.current.entries.at(-1)!)
    })
    const retried = calls.filter(
      (call) => call.path === "/assistant/conversation-turns"
    )
    expect(retried).toHaveLength(1)
    expect(retried[0].body).toEqual(body)
  })
  it("matches restored turns by request ID even when another turn has identical text", async () => {
    const body = {
      conversation_id: "123e4567-e89b-42d3-a456-426614174000",
      request_id: "123e4567-e89b-42d3-a456-426614174001",
      expected_version: 1,
      instruction: "Thanks",
      timezone: "UTC",
      active_task_id: null
    }
    ;(chrome.storage.session.get as any).mockResolvedValue({
      "threadlyConversation:1": {
        id: body.conversation_id,
        version: 1,
        pendingTurn: { id: body.request_id, body }
      }
    })
    const calls = mock((m) =>
      m.path.startsWith("/assistant/conversations/")
        ? {
            conversation_id: body.conversation_id,
            version: 3,
            history: [
              {
                user: "Thanks",
                assistant: "First",
                request_id: body.request_id
              },
              { user: "Thanks", assistant: "Second", request_id: "other" }
            ],
            pending_request_id: null
          }
        : respond(m)
    )
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(result.current.entries).toHaveLength(2)
    expect(result.current.entries[0].message).toBe("First")
    expect(result.current.canRetry(result.current.entries[0])).toBe(false)
    await act(async () => {
      await result.current.submit("Next")
    })
    expect(calls.at(-1).body.expected_version).toBe(3)
  })
  it("does not offer retry for a server turn owned by another request", async () => {
    const body = {
      conversation_id: "123e4567-e89b-42d3-a456-426614174000",
      request_id: "123e4567-e89b-42d3-a456-426614174001",
      expected_version: 0,
      instruction: "Find GYG",
      timezone: "UTC",
      active_task_id: null
    }
    ;(chrome.storage.session.get as any).mockResolvedValue({
      "threadlyConversation:1": {
        id: body.conversation_id,
        version: 0,
        pendingTurn: { id: body.request_id, body }
      }
    })
    mock((m) => ({
      conversation_id: body.conversation_id,
      version: 0,
      history: [],
      pending_request_id: "another-request"
    }))
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(result.current.entries[0].error).toContain("could not be matched")
    expect(result.current.canRetry(result.current.entries[0])).toBe(false)
  })
  it("keeps source references stable and explicitly clears a removed pin", async () => {
    const calls = mock((m) =>
      m.path.endsWith("context-snapshots")
        ? { context_snapshot_id: "ctx" }
        : m.path.startsWith("/threads/")
          ? {
              thread: { thread_id: "t", version: 1 },
              messages: [{ gmail_msg_id: "m" }]
            }
          : respond(m)
    )
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      result.current.setSelection({
        thread: { thread_id: "t", version: 1 },
        messages: [{ gmail_msg_id: "m" }],
        selectedIds: ["m"],
        targetId: "m"
      } as any)
    })
    await act(async () => {
      await result.current.submit("Does this need a reply?")
    })
    await act(async () => {
      await result.current.submit("you tell me")
    })
    expect(
      calls.filter((c) => c.path.endsWith("context-snapshots"))
    ).toHaveLength(1)
    expect(calls.at(-1).body.context_snapshot_id).toBe("ctx")
    await act(async () => {
      result.current.setSelection(null)
    })
    await act(async () => {
      await result.current.submit("hey")
    })
    expect(calls.at(-1).body.context_snapshot_id).toBeNull()
  })
  it("keeps an email detached when the panel reopens before the next turn", async () => {
    const key = "threadlyConversation:1"
    const saved: Record<string, any> = {
      [key]: { id: "conversation-1", version: 1, pendingTurn: null }
    }
    ;(chrome.storage.session.get as any).mockImplementation(
      async (name: string) => (name in saved ? { [name]: saved[name] } : {})
    )
    ;(chrome.storage.session.set as any).mockImplementation(
      async (values: any) => {
        Object.assign(saved, values)
      }
    )
    ;(chrome.storage.session.remove as any).mockImplementation(
      async (name: string) => {
        delete saved[name]
      }
    )
    const calls = mock((m) => {
      if (m.path === "/assistant/conversations/conversation-1")
        return {
          conversation_id: "conversation-1",
          version: 1,
          history: [{ user: "Read this", assistant: "I found the email." }],
          context_snapshot_id: "ctx-1"
        }
      if (m.path === "/assistant/context-snapshots/ctx-1")
        return {
          thread_id: "thread-1",
          thread_version: 1,
          ui_map: {
            visible_message_ids: ["message-1"],
            selected_message_ids: ["message-1"]
          }
        }
      if (m.path === "/threads/thread-1")
        return {
          thread: { thread_id: "thread-1", version: 1, subject: "Receipt" },
          messages: [{ gmail_msg_id: "message-1", subject: "Receipt" }]
        }
      return respond(m)
    })
    const first = renderHook(() => useAssistant(user))
    await waitFor(() =>
      expect(first.result.current.selection?.thread.subject).toBe("Receipt")
    )
    await act(async () => {
      first.result.current.clearContext()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(first.result.current.selection).toBeNull()
    first.unmount()

    const reopened = renderHook(() => useAssistant(user))
    await waitFor(() =>
      expect(reopened.result.current.entries[0]?.message).toBe(
        "I found the email."
      )
    )
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(reopened.result.current.selection).toBeNull()
    await act(async () => {
      await reopened.result.current.submit("Show me recent emails")
    })
    const turn = calls
      .filter((call) => call.path === "/assistant/conversation-turns")
      .at(-1)
    expect(turn?.body.context_snapshot_id).toBeNull()
    expect(turn?.body.expected_version).toBe(1)
    reopened.unmount()
  })
  it("does not reattach an email whose restore fetch finishes after detach", async () => {
    const saved = {
      id: "conversation-1",
      version: 1,
      pendingTurn: null,
      selectionOverride: {
        threadId: "new-thread",
        threadVersion: 3,
        visibleMessageIds: ["new-message"],
        selectedIds: ["new-message"],
        targetId: "new-message"
      }
    }
    ;(chrome.storage.session.get as any).mockResolvedValue({
      "threadlyConversation:1": saved
    })
    let finishThread!: (value: any) => void
    const delayedThread = new Promise<any>((resolve) => {
      finishThread = resolve
    })
    const calls = mock((m) => {
      if (m.path === "/assistant/conversations/conversation-1")
        return {
          conversation_id: "conversation-1",
          version: 1,
          history: [],
          context_snapshot_id: "ctx-old"
        }
      if (m.path === "/threads/new-thread") return delayedThread
      return respond(m)
    })
    const { result } = renderHook(() => useAssistant(user))
    await waitFor(() =>
      expect(calls.some((call) => call.path === "/threads/new-thread")).toBe(
        true
      )
    )
    await act(async () => {
      result.current.clearContext()
    })
    await act(async () => {
      finishThread({
        thread: { thread_id: "new-thread", version: 3, subject: "New receipt" },
        messages: [{ gmail_msg_id: "new-message" }]
      })
      await delayedThread
    })
    await waitFor(() => expect(result.current.restoring).toBe(false))
    expect(result.current.selection).toBeNull()
    await act(async () => {
      await result.current.submit("Show me recent emails")
    })
    const turn = calls
      .filter((call) => call.path === "/assistant/conversation-turns")
      .at(-1)
    expect(turn?.body.context_snapshot_id).toBeNull()
  })
  it("shows restoration in progress until the saved conversation finishes loading", async () => {
    ;(chrome.storage.session.get as any).mockResolvedValue({
      "threadlyConversation:1": {
        id: "conversation-1",
        version: 1,
        pendingTurn: null
      }
    })
    let finishConversation!: (value: any) => void
    const delayedConversation = new Promise<any>((resolve) => {
      finishConversation = resolve
    })
    const calls = mock((m) =>
      m.path === "/assistant/conversations/conversation-1"
        ? delayedConversation
        : respond(m)
    )
    const { result } = renderHook(() => useAssistant(user))
    expect(result.current.restoring).toBe(true)
    await waitFor(() =>
      expect(
        calls.some(
          (call) => call.path === "/assistant/conversations/conversation-1"
        )
      ).toBe(true)
    )
    expect(result.current.restoring).toBe(true)
    await act(async () => {
      finishConversation({
        conversation_id: "conversation-1",
        version: 1,
        history: [],
        context_snapshot_id: null
      })
      await delayedConversation
    })
    await waitFor(() => expect(result.current.restoring).toBe(false))
  })
  it("keeps a saved chat after a transient restore failure until an explicit new chat", async () => {
    const key = "threadlyConversation:1"
    const pointer = { id: "conversation-1", version: 1, pendingTurn: null }
    const saved: Record<string, any> = { [key]: pointer }
    ;(chrome.storage.session.get as any).mockImplementation(
      async (name: string) => (name in saved ? { [name]: saved[name] } : {})
    )
    ;(chrome.storage.session.set as any).mockImplementation(
      async (values: Record<string, any>) => {
        Object.assign(saved, values)
      }
    )
    ;(chrome.storage.session.remove as any).mockImplementation(
      async (name: string) => {
        delete saved[name]
      }
    )
    vi.mocked(chrome.tabs.query).mockResolvedValue([])
    const calls: any[] = []
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((async (
      m: any
    ) => {
      calls.push(m)
      if (m.path === "/assistant/conversations/conversation-1")
        return {
          ok: false,
          error: {
            code: "service_unavailable",
            message: "Try again shortly.",
            status: 503
          }
        }
      return { ok: true, data: respond(m) }
    }) as any)
    const { result } = renderHook(() => useAssistant(user))
    await waitFor(() => expect(result.current.restoreFailed).toBe(true))
    expect(result.current.restoring).toBe(false)
    expect(saved[key]).toEqual(pointer)
    expect(
      calls.some((call) => call.path === "/assistant/conversation-turns")
    ).toBe(false)

    await act(async () => {
      await result.current.newChat()
    })
    expect(result.current.restoreFailed).toBe(false)
    expect(saved[key]).toBeUndefined()
  })
  it("restores a replacement email instead of an older server pin", async () => {
    const key = "threadlyConversation:1"
    const saved: Record<string, any> = {
      [key]: { id: "conversation-1", version: 1, pendingTurn: null }
    }
    ;(chrome.storage.session.get as any).mockImplementation(
      async (name: string) => (name in saved ? { [name]: saved[name] } : {})
    )
    ;(chrome.storage.session.set as any).mockImplementation(
      async (values: Record<string, any>) => {
        Object.assign(saved, values)
      }
    )
    ;(chrome.storage.session.remove as any).mockImplementation(
      async (name: string) => {
        delete saved[name]
      }
    )
    const calls = mock((m) => {
      if (m.path === "/assistant/conversations/conversation-1")
        return {
          conversation_id: "conversation-1",
          version: 1,
          history: [{ user: "Read this", assistant: "I found the old email." }],
          context_snapshot_id: "ctx-old"
        }
      if (m.path === "/assistant/context-snapshots/ctx-old")
        return {
          thread_id: "old-thread",
          thread_version: 1,
          ui_map: {
            visible_message_ids: ["old-message"],
            selected_message_ids: ["old-message"]
          }
        }
      if (m.path === "/threads/old-thread")
        return {
          thread: { thread_id: "old-thread", version: 1, subject: "Old order" },
          messages: [{ gmail_msg_id: "old-message" }]
        }
      if (m.path === "/threads/new-thread")
        return {
          thread: { thread_id: "new-thread", version: 3, subject: "New order" },
          messages: [{ gmail_msg_id: "new-message" }]
        }
      if (m.path === "/assistant/context-snapshots") {
        expect(m.body.thread_id).toBe("new-thread")
        return { context_snapshot_id: "ctx-new" }
      }
      return respond(m)
    })
    const first = renderHook(() => useAssistant(user))
    await waitFor(() =>
      expect(first.result.current.selection?.thread.subject).toBe("Old order")
    )
    await act(async () => {
      first.result.current.clearContext()
      await first.result.current.chooseEmail({
        thread_id: "new-thread",
        message_id: "new-message"
      } as any)
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(first.result.current.selection?.thread.subject).toBe("New order")
    first.unmount()

    const reopened = renderHook(() => useAssistant(user))
    await waitFor(() =>
      expect(reopened.result.current.selection?.thread.subject).toBe(
        "New order"
      )
    )
    expect(reopened.result.current.contextBlocked).toBe(false)
    await act(async () => {
      await reopened.result.current.submit("Summarise this new email")
    })
    const turn = calls
      .filter((call) => call.path === "/assistant/conversation-turns")
      .at(-1)
    expect(turn?.body.context_snapshot_id).toBe("ctx-new")
    reopened.unmount()
  })
  it("blocks a restored missing pin until the user explicitly clears it", async () => {
    ;(chrome.storage.session.get as any).mockResolvedValue({
      "threadlyConversation:1": { id: "c", version: 1 }
    })
    const calls = mock((m) => {
      if (m.path === "/assistant/conversations/c")
        return {
          conversation_id: "c",
          version: 1,
          history: [],
          context_snapshot_id: "missing"
        }
      if (m.path.endsWith("/missing")) throw new Error("Unavailable")
      return respond(m)
    })
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(result.current.contextBlocked).toBe(true)
    await act(async () => {
      await result.current.submit("Summarise it")
    })
    expect(calls).toHaveLength(2)
    await act(async () => {
      result.current.clearContext()
    })
    await act(async () => {
      await result.current.submit("Hello")
    })
    expect(calls.at(-1).body.context_snapshot_id).toBeNull()
  })
  it("captures only the chosen search message from a multi-message thread", async () => {
    const calls = mock((m) =>
      m.path.startsWith("/threads/")
        ? {
            thread: { thread_id: "t", version: 1 },
            messages: [{ gmail_msg_id: "m1" }, { gmail_msg_id: "m2" }]
          }
        : m.path === "/assistant/context-snapshots"
          ? { context_snapshot_id: "ctx" }
          : respond(m)
    )
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.chooseEmail({
        thread_id: "t",
        message_id: "m2"
      } as any)
    })
    expect(
      result.current.selection?.messages.map((m) => m.gmail_msg_id)
    ).toEqual(["m2"])
    await act(async () => {
      await result.current.submit("Summarise this email")
    })
    expect(
      calls.find((m) => m.path === "/assistant/context-snapshots").body.ui_map
    ).toMatchObject({
      visible_message_ids: ["m2"],
      selected_message_ids: ["m2"]
    })
  })
  it("keeps the pinned source when attach-current finds no open Gmail email", async () => {
    const calls = mock((m) =>
      m.path.endsWith("context-snapshots")
        ? { context_snapshot_id: "ctx" }
        : m.path.startsWith("/threads/")
          ? {
              thread: { thread_id: "t", version: 1 },
              messages: [{ gmail_msg_id: "m" }]
            }
          : respond(m)
    )
    vi.mocked(chrome.tabs.query).mockResolvedValue([])
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      result.current.setSelection({
        thread: { thread_id: "t", version: 1 },
        messages: [{ gmail_msg_id: "m" }],
        selectedIds: ["m"],
        targetId: "m"
      } as any)
    })
    await act(async () => {
      await result.current.submit("Read this")
      await result.current.selectActive()
      await result.current.submit("Does it need a reply?")
    })
    expect(result.current.selection?.targetId).toBe("m")
    expect(calls.at(-1).body.context_snapshot_id).toBe("ctx")
  })
  it("restores server history after reopening without uploading client transcript", async () => {
    ;(chrome.storage.session.get as any).mockResolvedValue({
      "threadlyConversation:1": { id: "c", version: 1 }
    })
    const calls = mock((m) =>
      m.path === "/assistant/conversations/c"
        ? {
            conversation_id: "c",
            version: 2,
            history: [
              {
                user: "hi",
                assistant: "Hello",
                error_code: "calendar_preferences_stale"
              }
            ]
          }
        : respond(m)
    )
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await Promise.resolve()
    })
    expect(result.current.entries[0].message).toBe("Hello")
    expect(result.current.entries[0].errorCode).toBe(
      "calendar_preferences_stale"
    )
    await act(async () => {
      await result.current.submit("How are you?")
    })
    expect(calls.at(-1).body).toMatchObject({
      conversation_id: "c",
      expected_version: 2
    })
  })
  it("restores an active task on its matching exchange", async () => {
    ;(chrome.storage.session.get as any).mockResolvedValue({
      "threadlyConversation:1": { id: "c", version: 2 }
    })
    mock((m) =>
      m.path === "/assistant/conversations/c"
        ? {
            conversation_id: "c",
            version: 2,
            history: [
              { user: "Draft it", assistant: "Working", task_id: "t" },
              { user: "Thanks", assistant: "You’re welcome" }
            ],
            active_task_id: "t",
            context_snapshot_id: null
          }
        : {
            task_id: "t",
            instruction: "Draft it",
            state: "needs_clarification",
            question: { fields: ["recipients"] },
            artifact_id: null
          }
    )
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(result.current.entries[0].task?.task_id).toBe("t")
    expect(result.current.entries[1].task).toBeUndefined()
  })
  it("restores an active proposal through its owned review route", async () => {
    ;(chrome.storage.session.get as any).mockResolvedValue({
      "threadlyConversation:1": { id: "c", version: 1 }
    })
    const calls = mock((m) => {
      if (m.path === "/assistant/conversations/c")
        return {
          conversation_id: "c",
          version: 1,
          history: [
            {
              user: "Plan it",
              assistant: "Review these steps",
              request_id: "r1",
              proposal_id: "p1"
            }
          ],
          active_proposal_id: "p1"
        }
      if (m.path === "/assistant/workflow-proposals/p1")
        return {
          plan_id: "p1",
          state: "proposed",
          request: { instruction: "Plan it" }
        }
      return respond(m)
    })
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(calls.map((m) => m.path)).toEqual([
      "/assistant/conversations/c",
      "/assistant/workflow-proposals/p1"
    ])
    expect(result.current.entries).toHaveLength(1)
    expect(result.current.entries[0].proposal.plan_id).toBe("p1")
    await act(async () => {
      await result.current.submit("Can you make step two shorter?")
    })
    expect(calls.at(-1).body.active_task_id).toBeUndefined()
    expect(calls.at(-1).body.expected_version).toBe(1)
  })
  it("shows a successful response even if saving browser state fails afterward", async () => {
    ;(chrome.storage.session.set as any)
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error("Quota unavailable"))
    const calls = mock((m) => respond(m, { text: "Found it" }))
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.submit("Find it")
    })
    expect(calls).toHaveLength(1)
    expect(result.current.entries[0].message).toBe("Found it")
    expect(result.current.entries[0].notice).toContain("could not be saved")
    expect(result.current.canRetry(result.current.entries[0])).toBe(false)
  })
  it("pages only the latest search through a new conversation turn", async () => {
    let count = 0
    const calls = mock((m) =>
      respond(m, {
        search: {
          filters: { query: `q${++count}` },
          results: [{ message_id: `m${count}`, thread_id: "t" }],
          next_cursor: "next"
        }
      })
    )
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.submit("Search first")
      await result.current.submit("Search second")
    })
    expect(result.current.canPage(result.current.entries[0])).toBe(false)
    expect(result.current.canPage(result.current.entries[1])).toBe(true)
    await act(async () => {
      await result.current.moreEmails(result.current.entries[0])
    })
    expect(calls).toHaveLength(2)
    await act(async () => {
      await result.current.moreEmails(result.current.entries[1])
    })
    expect(calls.at(-1).path).toBe("/assistant/conversation-turns")
    expect(calls.at(-1).body.instruction).toBe(
      "Show me the next page of these emails"
    )
  })
  it("deletes stored conversation and starts a new identity", async () => {
    const calls = mock((m) =>
      m.method === "DELETE" ? { deleted: true } : respond(m)
    )
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.submit("hey")
    })
    await act(async () => {
      await result.current.deleteChat()
    })
    await act(async () => {
      await result.current.submit("hi again")
    })
    expect(calls[1].method).toBe("DELETE")
    expect(calls[2].body.conversation_id).not.toBe(
      calls[0].body.conversation_id
    )
  })
  it("deletes an uncertain first-turn ID even before a version is confirmed", async () => {
    const calls = mock((m) => {
      if (m.method === "DELETE") return { deleted: true }
      throw new Error("The response was lost")
    })
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.submit("Hello")
    })
    const id = calls[0].body.conversation_id
    await act(async () => {
      await result.current.deleteChat()
    })
    expect(calls[1]).toMatchObject({
      path: `/assistant/conversations/${id}`,
      method: "DELETE"
    })
    expect(result.current.entries).toHaveLength(0)
  })
  it("always clears busy after a history task cannot load", async () => {
    ;(chrome.storage.session.remove as any).mockRejectedValueOnce(
      new Error("Storage unavailable")
    )
    mock(() => ({}))
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.loadTask({
        task_id: "t",
        instruction: "Old task",
        state: "succeeded"
      } as any)
    })
    expect(result.current.busy).toBe(false)
    expect(result.current.error).toContain("Storage unavailable")
  })
})

it("keeps Calendar recovery codes from completed responses", async () => {
  mock((m) =>
    respond(m, {
      text: "Review your calendars.",
      error_code: "calendar_coverage_incomplete"
    })
  )
  const { result } = renderHook(() => useAssistant(user))
  await act(async () => {
    await result.current.submit("am i free on the tuesday")
  })
  expect(result.current.entries[0].errorCode).toBe(
    "calendar_coverage_incomplete"
  )
})

it("only advances the server-identified draft, expires old cards on a new goal, and preserves legacy conservatism", async () => {
  let active: string | null | undefined = "draft-one"
  mock((m) =>
    respond(m, {
      ...(m.body.expected_version === 0
        ? {
            email_draft: {
              draft_id: "draft-one",
              recipient: "Alex",
              subject: "Hello",
              body: "Hello",
              unresolved_fields: []
            }
          }
        : {}),
      ...(active === undefined ? {} : { active_email_draft_id: active })
    })
  )
  const { result } = renderHook(() => useAssistant(user))
  await act(async () => {
    await result.current.submit("Write an email to Alex")
  })
  await act(async () => {
    await result.current.submit("yes please save this draft")
  })
  expect(result.current.entries[0].conversationVersion).toBe(2)
  expect(result.current.canCreateDraft(result.current.entries[0])).toBe(true)
  active = null
  await act(async () => {
    await result.current.submit("Write another email to Priya")
  })
  expect(result.current.canCreateDraft(result.current.entries[0])).toBe(false)
  active = undefined
  await act(async () => {
    await result.current.submit("legacy server response")
  })
  expect(result.current.entries[0].conversationVersion).toBe(2)
  expect(result.current.canCreateDraft(result.current.entries[0])).toBe(false)
})

it("restores the active draft's current version without advancing unrelated history", async () => {
  ;(chrome.storage.session.get as any).mockResolvedValue({
    "threadlyConversation:1": { id: "c", version: 3 }
  })
  mock(() => ({
    conversation_id: "c",
    version: 3,
    active_email_draft_id: "draft-one",
    history: [
      {
        request_id: "r1",
        version: 1,
        user: "Compose",
        assistant: "Draft",
        email_draft: {
          draft_id: "draft-one",
          recipient: "Alex",
          subject: "Hello",
          body: "Hello",
          unresolved_fields: []
        }
      },
      {
        request_id: "r2",
        version: 2,
        user: "save it",
        assistant: "Click Create draft"
      },
      {
        request_id: "r3",
        version: 3,
        user: "yes",
        assistant: "Click Create draft"
      }
    ]
  }))
  const { result } = renderHook(() => useAssistant(user))
  await waitFor(() => expect(result.current.restoring).toBe(false))
  expect(result.current.entries[0].conversationVersion).toBe(3)
  expect(result.current.canCreateDraft(result.current.entries[0])).toBe(true)
  expect(result.current.entries[1].conversationVersion).toBe(2)
})

it("keeps backend goal focus instead of resending the last task card", async () => {
  let first = true
  const calls = mock((m) => {
    const task = first
      ? { task_id: "old-task", state: "needs_clarification" }
      : undefined
    first = false
    return respond(m, { context_memory_version: 1, task })
  })
  const { result } = renderHook(() => useAssistant(user))
  await act(async () => {
    await result.current.submit("Draft a message")
  })
  await act(async () => {
    await result.current.submit("Return to the earlier event")
  })
  expect(calls[1].body).not.toHaveProperty("active_task_id")
  expect(calls[1].body.instruction).toBe("Return to the earlier event")
})

it("restores a draft editor after its original exchange leaves compact history", async () => {
  ;(chrome.storage.session.get as any).mockResolvedValue({
    "threadlyConversation:1": { id: "c", version: 20 }
  })
  const draft = {
    draft_id: "retained",
    recipient: "Alex",
    subject: "Mosaic",
    body: "Thursday works",
    unresolved_fields: []
  }
  const calls = mock((m) =>
    m.path === "/assistant/conversations/c"
      ? {
          conversation_id: "c",
          version: 20,
          context_memory_version: 1,
          history: [
            {
              request_id: "recent",
              version: 20,
              user: "Thanks",
              assistant: "Welcome"
            }
          ],
          active_email_draft_id: "retained",
          active_email_draft: draft
        }
      : respond(m, { context_memory_version: 1 })
  )
  const { result } = renderHook(() => useAssistant(user))
  await waitFor(() => expect(result.current.restoring).toBe(false))
  const restored = result.current.entries.find((entry) => entry.emailDraft)
  expect(restored.emailDraft).toEqual(draft)
  expect(restored.conversationVersion).toBe(20)
  expect(result.current.canCreateDraft(restored)).toBe(true)
  await act(async () => {
    await result.current.submit("Return to Alex's message")
  })
  expect(calls.at(-1).body).not.toHaveProperty("active_task_id")
})

it("opens a returned saved draft after a Calendar detour without restarting work or approving actions", async () => {
  const artifact = {
    artifact_id: "saved-artifact",
    kind: "draft",
    revision: 2,
    payload: {
      kind: "draft",
      content: { subject: "Agenda", body: "Thanks for the agenda." }
    }
  }
  const calls = mock((m) => {
    if (m.path === "/assistant/artifacts/saved-artifact") return artifact
    if (m.path !== "/assistant/conversation-turns")
      throw new Error(`Unexpected ${m.path}`)
    return respond(m, {
      context_memory_version: 1,
      ...(m.body.expected_version === 0
        ? { text: "What time should Focus start?" }
        : m.body.expected_version === 1
          ? {
              text: "Here is your existing draft.",
              task_id: "saved-task",
              task: {
                task_id: "saved-task",
                state: "succeeded",
                instruction: "Draft a reply",
                artifact_id: "saved-artifact"
              }
            }
          : { text: "You're welcome." })
    })
  })
  const { result } = renderHook(() => useAssistant(user))
  await act(async () => {
    await result.current.submit("Create Focus tomorrow")
  })
  await act(async () => {
    await result.current.submit("Bring back the saved agenda reply draft")
  })
  await waitFor(() =>
    expect(result.current.entries[1].artifacts).toEqual([artifact])
  )
  expect(result.current.entries[1].task?.task_id).toBe("saved-task")
  await act(async () => {
    await result.current.submit("Thanks")
  })
  expect(calls.at(-1).body).not.toHaveProperty("active_task_id")
  expect(calls.map((call) => call.path)).toEqual([
    "/assistant/conversation-turns",
    "/assistant/conversation-turns",
    "/assistant/artifacts/saved-artifact",
    "/assistant/conversation-turns"
  ])
})

it("follows the open email before chat, pins its visible reply target, and preserves server focus after a switch", async () => {
  const firstId = "abcdef123456aa",
    secondId = "abcdef123456bb"
  let openId = firstId
  const source = (id: string) => ({
    thread: {
      thread_id: id,
      subject: id === firstId ? "First email" : "Second email",
      version: 1
    },
    messages: [
      {
        gmail_msg_id: `${id}-old`,
        is_from_user: false,
        received_at: "2026-10-07T01:00:00Z"
      },
      {
        gmail_msg_id: `${id}-new`,
        is_from_user: false,
        received_at: "2026-10-07T02:00:00Z"
      },
      {
        gmail_msg_id: `${id}-mine`,
        is_from_user: true,
        received_at: "2026-10-07T03:00:00Z"
      },
      {
        gmail_msg_id: `${id}-hidden`,
        is_from_user: false,
        received_at: "2026-10-07T04:00:00Z"
      }
    ]
  })
  vi.mocked(chrome.tabs.query).mockResolvedValue([
    { id: 1, url: "https://mail.google.com/mail/u/0/" }
  ] as any)
  vi.mocked(chrome.tabs.sendMessage).mockImplementation(async () => ({
    accountEmail: user.email,
    threadId: openId,
    subject: source(openId).thread.subject,
    messageIds: [`${openId}-old`, `${openId}-new`, `${openId}-mine`],
    selectedMessageId: null
  }))
  const calls = mock((m) => {
    if (m.path.startsWith("/threads/")) return source(m.path.split("/").at(-1))
    if (m.path === "/assistant/context-snapshots")
      return { context_snapshot_id: `context-${m.body.thread_id}` }
    return respond(m, { context_memory_version: 1 })
  })
  const { result } = renderHook(() => useAssistant(user))
  await waitFor(() => expect(result.current.restoring).toBe(false))
  await act(async () => {
    await result.current.followActive(true)
  })
  expect(result.current.selection?.thread.thread_id).toBe(firstId)
  openId = secondId
  await act(async () => {
    await result.current.followActive(true)
  })
  expect(result.current.selection?.thread.thread_id).toBe(secondId)
  expect(result.current.selection?.targetId).toBe(`${secondId}-new`)
  await act(async () => {
    await result.current.submit("Draft a reply")
  })
  const capture = calls.find((m) => m.path === "/assistant/context-snapshots")
  expect(capture.body).toMatchObject({
    thread_id: secondId,
    ui_map: { selected_message_ids: [`${secondId}-new`] }
  })
  expect(capture.body.ui_map.visible_message_ids).not.toContain(
    `${secondId}-hidden`
  )
  openId = firstId
  const beforeObservation = calls.length
  await act(async () => {
    await result.current.followActive(true)
  })
  expect(calls).toHaveLength(beforeObservation)
  expect(result.current.openElsewhere).toBe("First email")
  expect(result.current.selection?.thread.thread_id).toBe(secondId)
  await act(async () => {
    await result.current.submit("Return to the earlier event")
  })
  const turns = calls.filter((m) => m.path === "/assistant/conversation-turns")
  expect(turns[1].body).not.toHaveProperty("active_task_id")
  expect(turns[1].body).not.toHaveProperty("context_snapshot_id")
  await act(async () => {
    await result.current.newChat()
  })
  expect(result.current.entries).toEqual([])
  expect(result.current.openElsewhere).toBeNull()
  expect(result.current.selection?.thread.thread_id).toBe(firstId)
  await act(async () => {
    await result.current.submit("Summarise this email")
  })
  const last = calls.at(-1).body
  expect(last.conversation_id).not.toBe(turns[0].body.conversation_id)
  expect(last.context_snapshot_id).toBe(`context-${firstId}`)
})
