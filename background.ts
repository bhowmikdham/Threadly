chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({
    openPanelOnActionClick: true
  })
})

chrome.runtime.onMessage.addListener((message, sender) => {
  if (message.type === "OPEN_SIDE_PANEL" && sender.tab?.windowId) {
    chrome.sidePanel.open({ windowId: sender.tab.windowId })
  }
})