import type { PlasmoCSConfig } from "plasmo"

import { readGmailSelection } from "./lib/gmail-context"

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
      selected.threadId !== request.threadId
    ) {
      respond({
        ok: false,
        message:
          "The selected thread changed. Open its reply editor and try again."
      })
      return false
    }
    const containers = Array.from(
      document.querySelectorAll("[data-legacy-message-id]")
    ).filter(
      (el) => el.getAttribute("data-legacy-message-id") === request.messageId
    )
    const editors = containers
      .flatMap((el) =>
        Array.from(
          el.querySelectorAll<HTMLElement>(
            '[contenteditable="true"][role="textbox"]'
          )
        )
      )
      .filter((el) => el.getClientRects().length)
    if (editors.length !== 1 || editors[0].innerText.trim()) {
      respond({
        ok: false,
        message:
          "Open one empty reply editor on the selected message, or copy the draft instead."
      })
      return false
    }
    editors[0].focus()
    editors[0].textContent = request.body
    editors[0].dispatchEvent(
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
