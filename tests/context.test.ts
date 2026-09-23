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
