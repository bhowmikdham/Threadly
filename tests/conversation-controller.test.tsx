import { act, renderHook } from "@testing-library/react"
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
            history: [{ user: "hi", assistant: "Hello" }]
          }
        : respond(m)
    )
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await Promise.resolve()
    })
    expect(result.current.entries[0].message).toBe("Hello")
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
