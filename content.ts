import type { PlasmoCSConfig } from "plasmo"

// Match only Gmail tabs
export const config: PlasmoCSConfig = {
  matches: ["https://mail.google.com/*"]
}

// Listen for scrape requests from the Side Panel
chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (request.action === "GET_OPEN_EMAIL_TEXT") {
    // Gmail email text containers are identified by the 'div.a3s' class
    const messageElements = Array.from(document.querySelectorAll("div.a3s"))

    if (messageElements.length === 0) {
      sendResponse({ evidence: null })
      return true
    }

    const evidence = messageElements
      .map((el, index) => {
        const text = (el as HTMLElement).innerText.trim()
        return {
          evidenceId: `ev_${index + 1}`,
          messageId: `dom_msg_${index + 1}`,
          author: `Message ${index + 1}`,
          date: new Date().toISOString(),
          snippet: text.slice(0, 3000)
        }
      })
      .filter((item) => item.snippet.length > 0)

    sendResponse({ evidence: evidence.length > 0 ? evidence : null })
  }
  return true
})