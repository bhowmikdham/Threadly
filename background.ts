export {}

const GEMINI_API_KEY = process.env.PLASMO_PUBLIC_GEMINI_API_KEY; // 👈 Add your Gemini API Key here

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({
    openPanelOnActionClick: true
  })
})

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "OPEN_SIDE_PANEL" && sender.tab?.windowId) {
    chrome.sidePanel.open({ windowId: sender.tab.windowId })
  }

  // 👇 new handler goes here
  if (message.type === "inboxsdk__injectPageWorld" && sender.tab?.id) {
    chrome.scripting
      .executeScript({
        target: { tabId: sender.tab.id },
        world: "MAIN",
        files: ["pageWorld.js"]
      })
      .then(() => sendResponse(true))
      .catch((err) => {
        console.error("MailMind: failed to inject pageWorld.js", err)
        sendResponse(false)
      })
    return true // keeps the message channel open for the async response
  }

  if (message.type === "CLASSIFY_EMAIL") {
    const prompt = `Classify this email's priority (HIGH, MEDIUM, LOW), and determine whether the recipient needs to personally take action (reply, approve, complete a task, make a decision) versus it being purely informational.
Subject: ${message.subject}
Snippet: ${message.snippet}

Respond strictly in JSON matching this schema:
{
  "priority": "HIGH" | "MEDIUM" | "LOW",
  "actionRequired": boolean,
  "category": "LEGAL" | "HR" | "ACADEMIC" | "MEETING" | "FINANCE" | "OTHER"
}`

    fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${GEMINI_API_KEY}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contents: [{ parts: [{ text: prompt }] }],
          generationConfig: { responseMimeType: "application/json" }
        })
      }
    )
      .then((res) => res.json())
      .then((data) => {
        const rawText = data.candidates?.[0]?.content?.parts?.[0]?.text ?? "{}"
        const parsed = JSON.parse(rawText)
        sendResponse({
        success: true,
        data: {
        priority: parsed.priority || "MEDIUM",
        actionRequired: typeof parsed.actionRequired === "boolean" ? parsed.actionRequired : false,
        category: parsed.category || "OTHER"
        }
      })
      })
      .catch((err) => {
        console.error("Gemini Classification Error:", err)
        sendResponse({ success: false, data: { priority: "LOW", actionRequired: false, category: "OTHER" } })
      })

    return true // Keeps response channel open for async fetch
  }
})

