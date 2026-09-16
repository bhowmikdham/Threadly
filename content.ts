import type { PlasmoCSConfig } from "plasmo"

// Match only Gmail tabs
export const config: PlasmoCSConfig = {
  matches: ["https://mail.google.com/*"]
}

interface ScrapedEvidenceItem {
  evidenceId: string
  messageId: string
  author: string
  date: string
  snippet: string
}

// Finds the message "row" wrapping a given message body element, so we can read the
// sender/date info that lives alongside the body rather than inside it.
const findMessageContainer = (bodyEl: Element): Element | null => {
  return (
    bodyEl.closest('[role="listitem"]') ||
    bodyEl.closest(".adn.ads") ||
    bodyEl.closest(".adn") ||
    bodyEl.parentElement
  )
}

// Gmail renders the sender's real address as a hidden `email` attribute on the sender
// span, even though only the display name is shown visually:
// <span class="gD" email="jane@example.com" name="Jane Doe">Jane Doe</span>
const extractSenderEmail = (container: Element | null): string | null => {
  if (!container) return null
  const senderEl = container.querySelector("span.gD[email], span[email]")
  return senderEl?.getAttribute("email") || null
}

const extractSenderName = (container: Element | null): string | null => {
  if (!container) return null
  const senderEl = container.querySelector("span.gD[name], span[name]")
  return senderEl?.getAttribute("name") || senderEl?.textContent?.trim() || null
}

// The full timestamp usually lives in a `title` attribute on a nearby span, since the
// visible text is often an abbreviated relative date ("2:45 PM", "Sep 12").
const extractMessageDate = (container: Element | null): string | null => {
  if (!container) return null
  const dateEl = container.querySelector("span.g3[title], span[title]")
  return dateEl?.getAttribute("title") || dateEl?.textContent?.trim() || null
}

// Extracts the specific message ID directly attached to the Gmail message container card
const extractMessageId = (container: Element | null): string | null => {
  if (!container) return null
  return (
    container.getAttribute("data-message-id") ||
    container.getAttribute("data-legacy-message-id") ||
    null
  )
}

// Extracts the thread ID directly from Gmail DOM attributes or URL hashes.
// Ensures strict matching against Gmail API's required 16-character hex format.
const extractLegacyThreadId = (): string | null => {
  // Method 1: Check standard Gmail DOM attribute for open threads
  const threadContainer = document.querySelector('[data-legacy-thread-id]')
  if (threadContainer) {
    const threadId = threadContainer.getAttribute('data-legacy-thread-id')
    if (threadId && /^[0-9a-fA-F]{16}$/.test(threadId)) {
      return threadId
    }
  }

  // Method 2: Inspect links containing th= parameters for a 16-char hex string
  const links = Array.from(document.querySelectorAll<HTMLAnchorElement>('a[href*="th="]'))

  const threadScoped = links.find((link) => link.href.includes("view=pt"))
  if (threadScoped) {
    const match = threadScoped.href.match(/[?&]th=([0-9a-fA-F]{16})/)
    if (match) return match[1]
  }

  for (const link of links) {
    const match = link.href.match(/[?&]th=([0-9a-fA-F]{16})/)
    if (match) return match[1]
  }

  // Method 3: Fallback check directly in the location hash (e.g., #inbox/18e4a2b9f01c3d4a)
  const hashMatch = window.location.hash.match(/\/([0-9a-fA-F]{16})/)
  if (hashMatch) {
    return hashMatch[1]
  }

  return null
}

// The open thread's subject line, rendered as a page heading above the messages.
const extractSubject = (): string | null => {
  const subjectEl = document.querySelector("h2.hP")
  return subjectEl?.textContent?.trim() || null
}

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (request.action === "GET_OPEN_EMAIL_TEXT") {
    // Gmail email text containers are identified by the 'div.a3s' class
    const messageElements = Array.from(document.querySelectorAll("div.a3s"))

    if (messageElements.length === 0) {
      sendResponse({ evidence: null })
      return true
    }

    const evidence: ScrapedEvidenceItem[] = messageElements
      .map((el, index) => {
        const text = (el as HTMLElement).innerText.trim()
        const container = findMessageContainer(el)
        const senderName = extractSenderName(container)
        const senderEmail = extractSenderEmail(container)
        const date = extractMessageDate(container)

        // Prefer "Name <email>" when we have both — summarization reads this field too,
        // and benefits from a real identity instead of a placeholder like "Message 1".
        const author =
          senderName && senderEmail
            ? `${senderName} <${senderEmail}>`
            : senderName || senderEmail || `Message ${index + 1}`

        return {
          evidenceId: `ev_${index + 1}`,
          messageId: `dom_msg_${index + 1}`,
          author,
          date: date || new Date().toISOString(),
          snippet: text.slice(0, 3000)
        }
      })
      .filter((item) => item.snippet.length > 0)

    if (evidence.length === 0) {
      sendResponse({ evidence: null })
      return true
    }

    // --- FIX: TARGET CURRENTLY OPEN/EXPANDED MESSAGE ---
    // 1. First, check for an actively expanded email card in the DOM
    let activeContainer = document.querySelector('div.adn.ads[aria-expanded="true"]') 
      || document.querySelector('[role="listitem"][aria-expanded="true"]')
    
    // 2. Fall back to focused element container
    if (!activeContainer) {
      const focusedEl = document.querySelector('div.adn.ads:focus-within') || document.querySelector('[role="listitem"]:focus-within')
      if (focusedEl) {
        activeContainer = findMessageContainer(focusedEl)
      }
    }

    // 3. Fall back to the last message if all are collapsed or un-focused
    if (!activeContainer) {
      activeContainer = findMessageContainer(messageElements[messageElements.length - 1])
    }

    const authorEmail = extractSenderEmail(activeContainer)
    const activeMessageId = extractMessageId(activeContainer)
    const legacyThreadId = extractLegacyThreadId()
    const subject = extractSubject()

    sendResponse({ 
      evidence, 
      authorEmail, 
      legacyThreadId, 
      subject, 
      activeMessageId 
    })
  }
  return true
})