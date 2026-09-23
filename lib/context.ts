import { api } from "./api"
import type { Selection, User } from "./types"

export async function activeGmail(user: User): Promise<Selection | null> {
  const tabs = await chrome.tabs.query({
    active: true,
    lastFocusedWindow: true
  })
  const tab = tabs[0]
  if (!tab?.id || !tab.url?.startsWith("https://mail.google.com/")) return null
  let observed: any
  try {
    observed = await chrome.tabs.sendMessage(tab.id, {
      action: "THREADLY_SELECTION"
    })
  } catch {
    throw new Error(
      "Reload the Gmail tab after installing Threadly, then select the email again."
    )
  }
  if (
    observed.accountEmail &&
    observed.accountEmail.toLowerCase() !== user.email.toLowerCase()
  )
    throw new Error(
      "The open Gmail account differs from your connected Threadly account. Switch accounts before selecting this thread."
    )
  if (!observed.threadId) return null
  const data = await api(`/threads/${encodeURIComponent(observed.threadId)}`)
  const ids = new Set(data.messages.map((m) => m.gmail_msg_id))
  const selectedIds = observed.messageIds.filter((id) => ids.has(id))
  // Never replace unresolved DOM ordinals with the last message or silently guess a reply target.
  return {
    ...data,
    selectedIds,
    targetId: ids.has(observed.selectedMessageId)
      ? observed.selectedMessageId
      : null
  }
}
export async function capture(selection: Selection) {
  if (!selection.selectedIds.length)
    throw new Error("Select one or more messages for this request.")
  const data = await api(
    `/threads/${encodeURIComponent(selection.thread.thread_id)}`
  )
  if (data.thread.version !== selection.thread.version)
    throw new Error(
      "This thread changed. Refresh the selection before continuing."
    )
  const ids = selection.messages
    .filter((m) => selection.selectedIds.includes(m.gmail_msg_id))
    .map((m) => m.gmail_msg_id)
  if (!ids.every((id) => data.messages.some((m) => m.gmail_msg_id === id)))
    throw new Error("A selected message is no longer available.")
  return api("/assistant/context-snapshots", {
    schema_version: "1.1",
    thread_id: data.thread.thread_id,
    ui_map: {
      schema_version: "1.0",
      surface: "gmail_thread",
      thread_version: data.thread.version,
      captured_at: new Date().toISOString(),
      visible_message_ids: ids,
      selected_message_ids: selection.targetId ? [selection.targetId] : ids
    }
  })
}
