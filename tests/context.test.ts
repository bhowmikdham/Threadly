import { describe, expect, it, vi } from "vitest"

import { activeGmail, capture } from "../lib/context"

const selection: any = {
  thread: { thread_id: "thread1", subject: "Receipt", version: 4 },
  messages: [
    { gmail_msg_id: "m1" },
    { gmail_msg_id: "m2" },
    { gmail_msg_id: "m3" }
  ],
  selectedIds: ["m3", "m1"],
  targetId: "m3"
}
describe("reference-only source capture", () => {
  it("keeps Gmail's displayed order even when provider messages arrive chronologically", async () => {
    vi.mocked(chrome.tabs.query).mockResolvedValue([
      { id: 1, url: "https://mail.google.com/mail/u/0/" }
    ] as any)
    vi.mocked(chrome.tabs.sendMessage).mockResolvedValue({
      accountEmail: "owner@example.test",
      threadId: "thread1",
      messageIds: ["m3", "m1"],
      selectedMessageId: "m3"
    } as any)
    vi.mocked(chrome.runtime.sendMessage).mockResolvedValue({
      ok: true,
      data: selection
    } as any)
    const s = await activeGmail({
      id: 1,
      email: "owner@example.test",
      name: null
    })
    expect(s.messages.map((m) => m.gmail_msg_id)).toEqual(["m3", "m1", "m2"])
    expect(s.selectedIds).toEqual(["m3", "m1"])
  })
  it("preserves displayed source order and binds an explicit target independently", async () => {
    const calls: any[] = []
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((async (
      m: any
    ) => {
      calls.push(m)
      return {
        ok: true,
        data: m.path.startsWith("/threads/")
          ? selection
          : { context_snapshot_id: "context" }
      }
    }) as any)
    await capture(selection)
    expect(calls[1].body.ui_map.visible_message_ids).toEqual(["m1", "m3"])
    expect(calls[1].body.ui_map.selected_message_ids).toEqual(["m3"])
    expect(calls[1].body).not.toHaveProperty("messages")
  })
  it("rejects a stale source version before creating a task context", async () => {
    vi.mocked(chrome.runtime.sendMessage).mockResolvedValue({
      ok: true,
      data: { ...selection, thread: { ...selection.thread, version: 5 } }
    } as any)
    await expect(capture(selection)).rejects.toThrow("thread changed")
    expect(chrome.runtime.sendMessage).toHaveBeenCalledTimes(1)
  })
  it("does not fetch a Gmail thread through the wrong connected account", async () => {
    vi.mocked(chrome.tabs.query).mockResolvedValue([
      { id: 1, url: "https://mail.google.com/mail/u/0/" }
    ] as any)
    vi.mocked(chrome.tabs.sendMessage).mockResolvedValue({
      accountEmail: "different@example.test",
      threadId: "thread1"
    } as any)
    await expect(
      activeGmail({ id: 1, email: "owner@example.test", name: null })
    ).rejects.toThrow("differs")
    expect(chrome.runtime.sendMessage).not.toHaveBeenCalled()
  })
})

describe("reply target when Gmail doesn't mark the open message", () => {
  const thread: any = {
    thread: { thread_id: "thread1", subject: "Discovery Meeting", version: 1 },
    messages: [
      {
        gmail_msg_id: "m1",
        is_from_user: true,
        received_at: "2026-08-27T01:05:00Z"
      },
      {
        gmail_msg_id: "m2",
        is_from_user: false,
        received_at: "2026-08-27T01:08:00Z"
      },
      {
        gmail_msg_id: "m3",
        is_from_user: true,
        received_at: "2026-08-27T01:09:00Z"
      },
      {
        gmail_msg_id: "m4",
        is_from_user: false,
        received_at: "2026-08-27T01:10:00Z"
      },
      {
        gmail_msg_id: "m5",
        is_from_user: true,
        received_at: "2026-08-27T01:11:00Z"
      }
    ]
  }
  const open = async (messages: any[], messageIds: string[]) => {
    vi.mocked(chrome.tabs.query).mockResolvedValue([
      { id: 1, url: "https://mail.google.com/mail/u/0/" }
    ] as any)
    vi.mocked(chrome.tabs.sendMessage).mockResolvedValue({
      accountEmail: "owner@example.test",
      threadId: "thread1",
      messageIds,
      selectedMessageId: null
    } as any)
    vi.mocked(chrome.runtime.sendMessage).mockResolvedValue({
      ok: true,
      data: { ...thread, messages }
    } as any)
    return activeGmail({ id: 1, email: "owner@example.test", name: null })
  }
  it("replies to the newest message from someone else, like Gmail's Reply", async () => {
    const s = await open(thread.messages, ["m1", "m2", "m3", "m4", "m5"])
    expect(s.targetId).toBe("m4")
  })
  it("only picks a message that is on screen", async () => {
    const s = await open(thread.messages, ["m1", "m2", "m3"])
    expect(s.targetId).toBe("m2")
  })
  it("still asks when every message is from the user", async () => {
    const mine = thread.messages.filter((m) => m.is_from_user)
    const s = await open(mine, ["m1", "m3", "m5"])
    expect(s.targetId).toBeNull()
  })
})

describe("open-email detection from the Gmail URL", async () => {
  const { gmailUrlShowsEmail } = await import("../lib/gmail-context")
  it("is true only when a single thread is open", () => {
    const base = "https://mail.google.com/mail/u/0/"
    expect(gmailUrlShowsEmail(base + "#inbox/FMfcgzQbfLvKxQrWnZtjbVbHkWqmLmZd")).toBe(true)
    expect(gmailUrlShowsEmail(base + "#label/Work/FMfcgzQbfLvKxQrWnZtjbVbHkWqmLmZd")).toBe(true)
    expect(gmailUrlShowsEmail(base + "#search/receipt/18c2f1a9b7d4e6f0")).toBe(true)
    expect(gmailUrlShowsEmail(base + "#inbox")).toBe(false)
    expect(gmailUrlShowsEmail(base + "#label/Work")).toBe(false)
    expect(gmailUrlShowsEmail(base + "#search/receipt")).toBe(false)
    expect(gmailUrlShowsEmail("https://example.com/#inbox/FMfcgzQbfLvKxQrWnZtjbVbHkWqmLmZd")).toBe(false)
    expect(gmailUrlShowsEmail(undefined)).toBe(false)
  })
})
