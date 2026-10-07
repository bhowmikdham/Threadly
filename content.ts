import type { PlasmoCSConfig } from "plasmo"

import {
  insertIntoReplyEditor,
  readGmailSelection,
  targetReplyEditor
} from "./lib/gmail-context"

export const config: PlasmoCSConfig = { matches: ["https://mail.google.com/*"] }
chrome.runtime.onMessage.addListener((request, sender, respond) => {
  if (
    sender.id !== chrome.runtime.id ||
    !sender.url?.startsWith(`chrome-extension://${chrome.runtime.id}/`)
  )
    return false
  if (request.action === "THREADLY_SELECTION") {
    respond(readGmailSelection(document, location.href))
    return false
  }
  if (request.action === "THREADLY_INSERT") {
    const selected = readGmailSelection(document, location.href)
    if (
      typeof request.body !== "string" ||
      !request.body.trim() ||
      request.body.length > 20000 ||
      !request.threadId ||
      selected.threadId !== String(request.threadId).toLowerCase()
    ) {
      respond({
        ok: false,
        message:
          "The open email changed. Open this email’s reply box and try again."
      })
      return false
    }
    const editor = targetReplyEditor(document)
    if (!editor) {
      respond({
        ok: false,
        message:
          "Click into the reply box you want to use, or copy the draft instead."
      })
      return false
    }
    // Adds text only; anything already in the box stays, and nothing is sent.
    insertIntoReplyEditor(editor, request.body)
    editor.focus()
    editor.dispatchEvent(
      new InputEvent("input", {
        bubbles: true,
        inputType: "insertText",
        data: request.body
      })
    )
    respond({ ok: true })
    return false
  }
  return false
})
