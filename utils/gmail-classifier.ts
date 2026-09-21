// utils/gmail-classifier.ts
import * as InboxSDK from "@inboxsdk/core"
import priorityHighIcon from "url:../assets/icons/high.svg"
import priorityMediumIcon from "url:../assets/icons/medium.svg"
import priorityLowIcon from "url:../assets/icons/low.svg"
import actionRequiredIcon from "url:../assets/icons/action.svg"
import legalIcon from "url:../assets/icons/legal.svg"
import hrIcon from "url:../assets/icons/hr.svg"
import academicIcon from "url:../assets/icons/academic.svg"
import meetingIcon from "url:../assets/icons/meeting.svg"
import financeIcon from "url:../assets/icons/finance.svg"
import otherIcon from "url:../assets/icons/other.svg"

const PRIORITY_ICONS: Record<string, { iconUrl: string; title: string }> = {
  HIGH: { iconUrl: priorityHighIcon, title: "High priority" },
  MEDIUM: { iconUrl: priorityMediumIcon, title: "Medium priority" },
  LOW: { iconUrl: priorityLowIcon, title: "Low priority" }
}

const ACTION_ICON = { iconUrl: actionRequiredIcon, title: "Action required" }

// Inline SVG dash, used when action is NOT required — avoids needing a separate asset file,
// and works with the CSS that hides label text (.av { display: none })
const NO_ACTION_ICON = {
  iconUrl:
    "data:image/svg+xml;utf8," +
    encodeURIComponent(
      `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">
        <rect x="3" y="7" width="10" height="2" rx="1" fill="#9aa0a6"/>
      </svg>`
    ),
  title: "No action required"
}

const CATEGORY_ICONS: Record<string, { iconUrl: string; title: string }> = {
  LEGAL: { iconUrl: legalIcon, title: "Legal" },
  HR: { iconUrl: hrIcon, title: "HR" },
  ACADEMIC: { iconUrl: academicIcon, title: "Academic" },
  MEETING: { iconUrl: meetingIcon, title: "Meeting" },
  FINANCE: { iconUrl: financeIcon, title: "Finance" },
  OTHER: { iconUrl: otherIcon, title: "Other" }
}

// InboxSDK writes `title` both as visible text AND as a `data-tooltip` attribute (which
// Gmail's own UI already renders as a native hover tooltip). This hides only the visible
// text node, leaving the data-tooltip attribute — and therefore the hover tooltip — intact.
// It also repositions the label pill further left/up within the thread row.
function injectLabelStyles() {
  if (document.getElementById("mailmind-label-style")) return
  const style = document.createElement("style")
  style.id = "mailmind-label-style"
  style.textContent = `
    .inboxsdk__thread_row_label .av { display: none !important; }
    .inboxsdk__thread_row_label:nth-last-of-type(2) .inboxsdk__button_icon {
     margin-left: -35px !important;
     margin-top: 1px !important;
   }
   .inboxsdk__thread_row_label:nth-last-of-type(1) .inboxsdk__button_icon {
     margin-left: 10px !important;
     margin-top: 1px !important;
   }
  `
  document.head.appendChild(style)
}

export async function initGmailClassifier() {
  console.log("🚀 MailMind: Requesting InboxSDK initialization...")
  injectLabelStyles()

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
              const fallbackData = { priority: "HIGH", actionRequired: true, category: "OTHER" }
              renderBadge(threadRowView, fallbackData)
              return
            }

            const classification = response.data || { priority: "MEDIUM", actionRequired: false, category: "OTHER" }
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
  classification: { priority?: string; actionRequired?: boolean; category?: string }
) {
  const priority = (classification?.priority || "MEDIUM").toUpperCase()
  const icon = PRIORITY_ICONS[priority] || PRIORITY_ICONS.MEDIUM

  threadRowView.addLabel({
    iconUrl: icon.iconUrl,
    title: icon.title,
    backgroundColor: "transparent"
  })

  const actionBadge = classification?.actionRequired ? ACTION_ICON : NO_ACTION_ICON
    threadRowView.addLabel({
      iconUrl: actionBadge.iconUrl,
      title: actionBadge.title,
      backgroundColor: "transparent"
    })

    const category = (classification?.category || "OTHER").toUpperCase()
    const categoryIcon = CATEGORY_ICONS[category] || CATEGORY_ICONS.OTHER
    threadRowView.addAttachmentIcon({
      iconUrl: categoryIcon.iconUrl,
      tooltip: categoryIcon.title
    })
  
}