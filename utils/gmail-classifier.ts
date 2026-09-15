// utils/gmail-classifier.ts
import * as InboxSDK from "@inboxsdk/core"

export async function initGmailClassifier() {
  console.log("🚀 MailMind: Requesting InboxSDK initialization...")

  try {
    const sdk = await InboxSDK.load(2, "sdk_Threadly_7297c785d8", {
      injectedScriptLoaded: true
    })

    console.log("⚡ MailMind: InboxSDK successfully loaded!", sdk)

    sdk.Lists.registerThreadRowViewHandler((threadRowView) => {
      const subject = threadRowView.getSubject() || "No Subject"
      const threadId = threadRowView.getThreadID()
      const cacheKey = `class_${threadId}`

      chrome.storage.local.get([cacheKey], (result) => {
        // 1. Use cached result if available
        if (result && result[cacheKey]) {
          renderBadge(threadRowView, result[cacheKey])
          return
        }

        // 2. Safely capture contacts
        const contacts = threadRowView.getContacts() || []
        const senderName = contacts[0]?.name || "Unknown"
        const snippetText = `${subject} from ${senderName}`

        // 3. Send message to background service worker
        chrome.runtime.sendMessage(
          { type: "CLASSIFY_EMAIL", subject, snippet: snippetText },
          (response) => {
            if (chrome.runtime.lastError || !response || !response.success) {
              // Default fallback badge while background worker/AI is offline
              const fallbackData = { priority: "HIGH", category: "Action Required" }
              renderBadge(threadRowView, fallbackData)
              return
            }

            const classification = response.data || { priority: "NORMAL", category: "General" }
            chrome.storage.local.set({ [cacheKey]: classification })
            renderBadge(threadRowView, classification)
          }
        )
      })
    })
  } catch (err) {
    console.error("❌ MailMind: InboxSDK load error:", err)
  }
}

function renderBadge(
  threadRowView: any,
  classification: { priority?: string; category?: string }
) {
  const priority = classification?.priority || "HIGH"
  const category = classification?.category || "Action Required"
  const isHigh = priority === "HIGH"

  threadRowView.addLabel({
    title: `${priority} • ${category}`,
    backgroundColor: isHigh ? "#e8590c" : "#0ca678",
    foregroundColor: "#ffffff"
  })
}