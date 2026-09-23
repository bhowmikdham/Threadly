import { act, renderHook } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { useAssistant } from "../lib/use-assistant"

const user = { id: 1, email: "me@example.test", name: "Tester" }
function mock(handler: (m: any) => any) {
  const calls: any[] = []
  vi.mocked(chrome.runtime.sendMessage).mockImplementation((async (m: any) => {
    calls.push(m)
    return {
      ok: true,
      data:
        m.path === "/assistant/inbox-chat"
          ? { kind: "continue" }
          : await handler(m)
    }
  }) as any)
  return calls
}
describe("conversation orchestration", () => {
  it("lets the backend route a natural command and requires a separate plan confirmation", async () => {
    const calls = mock((m) =>
      m.path === "/assistant/requests"
        ? {
            task_id: "t",
            state: "unsupported",
            route: {
              decision: {
                requested_action: "none",
                intent: "plan_schedule",
                operations: ["schedule"]
              }
            }
          }
        : m.path === "/calendar/preferences"
          ? { version: 2 }
          : m.path === "/assistant/workflow-proposals"
            ? { plan_id: "p", state: "proposed", request: m.body }
            : null
    )
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.submit("Find three slots tomorrow and draft a reply")
    })
    expect(
      calls.find((m) => m.path === "/assistant/requests").body.intent_hint
    ).toBeNull()
    expect(result.current.entries[0].proposal.plan_id).toBe("p")
    expect(calls.some((m) => m.path.endsWith("/confirm"))).toBe(false)
  })
  it("answers a saved recipient question in chat instead of classifying the address as a new task", async () => {
    const calls = mock((m) =>
      m.path === "/assistant/requests"
        ? {
            task_id: "t",
            state: "needs_clarification",
            question: {
              question_id: "q",
              expected_version: 4,
              fields: ["recipients"]
            }
          }
        : { task_id: "t", state: "succeeded" }
    )
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.submit("Write an email thanking Alex")
    })
    await act(async () => {
      await result.current.submit("alex@example.test")
    })
    expect(calls.filter((m) => m.path === "/assistant/requests")).toHaveLength(
      1
    )
    expect(
      calls.find((m) => m.path === "/assistant/tasks/t/inputs").body
    ).toMatchObject({
      question_id: "q",
      expected_version: 4,
      answer: { recipients: ["alex@example.test"] }
    })
    expect(result.current.entries).toHaveLength(1)
    expect(result.current.entries[0].answers).toEqual(["alex@example.test"])
  })
  it("routes a compound task to the planner after its missing inputs have been supplied", async () => {
    const calls = mock((m) =>
      m.path === "/assistant/requests"
        ? {
            task_id: "t",
            instruction: "Summary and reply",
            state: "needs_clarification",
            question: {
              question_id: "q",
              expected_version: 4,
              fields: ["recipients"]
            }
          }
        : m.path.endsWith("/inputs")
          ? {
              task_id: "t",
              state: "unsupported",
              effective_draft_input: {
                to: ["alex@example.test"],
                cc: [],
                bcc: []
              },
              route: {
                decision: {
                  requested_action: "none",
                  operations: ["summarise_thread", "draft_new"]
                }
              }
            }
          : m.path === "/calendar/preferences"
            ? {}
            : { plan_id: "p", state: "proposed" }
    )
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.submit("Summarise and draft an email")
    })
    await act(async () => {
      await result.current.submit("alex@example.test")
    })
    expect(
      calls.find((m) => m.path === "/assistant/workflow-proposals").body
        .draft_options.to
    ).toEqual(["alex@example.test"])
    expect(result.current.entries[0].proposal.plan_id).toBe("p")
  })
  it("rewrites only the explicitly selected message using the bounded read contract", async () => {
    const source = {
      thread: { thread_id: "thread", version: 1 },
      messages: [{ gmail_msg_id: "m1" }],
      selectedIds: ["m1"],
      targetId: "m1"
    }
    const calls = mock((m) =>
      m.path?.startsWith("/threads/")
        ? source
        : m.path === "/assistant/context-snapshots"
          ? { context_snapshot_id: "ctx" }
          : { task_id: "t", state: "succeeded" }
    )
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.submit("Rewrite selected message", "m1")
    })
    expect(calls).toHaveLength(0)
    await act(async () => {
      result.current.setSelection(source as any)
    })
    await act(async () => {
      await result.current.submit("Rewrite selected message", "m1")
    })
    expect(
      calls.find((m) => m.path === "/assistant/requests").body
    ).toMatchObject({
      intent_hint: "other",
      context_snapshot_id: "ctx",
      read_options: { operation: "transform_text", message_id: "m1" }
    })
  })
})

describe("inbox conversation boundaries", () => {
  it("does not capture the open message or create tasks for a greeting or inbox search", async () => {
    const calls: any[] = []
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((async (
      m: any
    ) => {
      calls.push(m)
      return {
        ok: true,
        data:
          m.body.instruction === "hey"
            ? { kind: "message", text: "Hey! What can I help you with?" }
            : {
                kind: "search",
                search: {
                  filters: { query: "GYG" },
                  results: [],
                  next_cursor: null
                }
              }
      }
    }) as any)
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      result.current.setSelection({
        thread: { thread_id: "unrelated" },
        messages: [{ gmail_msg_id: "m" }],
        selectedIds: ["m"],
        targetId: "m"
      } as any)
    })
    await act(async () => {
      await result.current.submit("hey")
    })
    await act(async () => {
      await result.current.submit("Show GYG emails")
    })
    expect(calls.map((m) => m.path)).toEqual([
      "/assistant/inbox-chat",
      "/assistant/inbox-chat"
    ])
    expect(result.current.entries[0].message).toMatch(/Hey/)
    expect(result.current.entries[1].inbox.results).toEqual([])
    expect(result.current.selection.thread.thread_id).toBe("unrelated")
  })
  it("keeps a failed page retry bound to its original filters and cursor, then deduplicates", async () => {
    const calls: any[] = []
    const first = { message_id: "m1", thread_id: "t1" }
    const filters = { query: "GYG", received_from: "2026-01-01T00:00:00Z" }
    let fail = true
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((async (
      m: any
    ) => {
      calls.push(m)
      if (m.path.endsWith("inbox-chat"))
        return {
          ok: true,
          data: {
            kind: "search",
            search: { filters, results: [first], next_cursor: "opaque-1" }
          }
        }
      if (fail) throw new Error("Connection interrupted")
      return {
        ok: true,
        data: {
          filters,
          results: [first, { message_id: "m2", thread_id: "t2" }],
          next_cursor: null
        }
      }
    }) as any)
    const { result } = renderHook(() => useAssistant(user))
    await act(async () => {
      await result.current.submit("Show GYG emails")
    })
    await act(async () => {
      await result.current.moreEmails(result.current.entries[0])
    })
    expect(result.current.entries[0].inbox.next_cursor).toBe("opaque-1")
    expect(result.current.entries[0].inbox.results).toEqual([first])
    fail = false
    await act(async () => {
      await result.current.moreEmails(result.current.entries[0])
    })
    expect(calls[1].body).toEqual({ filters, cursor: "opaque-1" })
    expect(calls[2].body).toEqual(calls[1].body)
    expect(
      result.current.entries[0].inbox.results.map((m) => m.message_id)
    ).toEqual(["m1", "m2"])
    expect(result.current.entries[0].inbox.next_cursor).toBeNull()
  })
})
