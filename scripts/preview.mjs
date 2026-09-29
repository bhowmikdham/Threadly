// Interactive offline preview of the real side panel.
//
//   npm run preview        load build/chrome-mv3-prod (run `npm run build` first)
//   npm run preview:dev    run `plasmo dev` and load build/chrome-mv3-dev with live reload
//
// Everything runs in a throwaway Chromium profile: a local mock backend built
// from the e2e fixtures (tests/e2e/mock-backend.ts) and the same synthetic
// session the offline suite seeds. Nothing here ships in the extension, and it
// never touches your own Chrome profile, Gmail, Google OAuth or AWS.
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { mkdtemp, readFile, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"

import { createMockBackend, user } from "../tests/e2e/mock-backend.ts"

// Use browsers installed inside node_modules when present, otherwise
// Playwright's default cache (`npx playwright install chromium`).
if (
  !process.env.PLAYWRIGHT_BROWSERS_PATH &&
  existsSync("node_modules/playwright-core/.local-browsers")
)
  process.env.PLAYWRIGHT_BROWSERS_PATH = "0"
const { chromium } = await import("@playwright/test")

const dev = process.argv.includes("--dev")
const port = Number(process.env.THREADLY_PREVIEW_PORT || 8787)
const extension = path.resolve(
  dev ? "build/chrome-mv3-dev" : "build/chrome-mv3-prod"
)

const { server } = createMockBackend((label, actual, expected) => {
  if (JSON.stringify(actual) !== JSON.stringify(expected))
    console.warn(`[mock] fixture contract drifted: ${label}`, {
      actual,
      expected
    })
})
await new Promise((resolve, reject) =>
  server.once("error", reject).listen(port, "127.0.0.1", resolve)
)
const origin = `http://127.0.0.1:${port}`

let plasmo = null
if (dev) {
  plasmo = spawn("npx", ["plasmo", "dev"], { stdio: "inherit" })
  // Wait for the first dev build before loading it.
  while (!existsSync(path.join(extension, "manifest.json")))
    await new Promise((r) => setTimeout(r, 500))
} else if (!existsSync(path.join(extension, "manifest.json"))) {
  console.error("No production build found. Run `npm run build` first.")
  server.close()
  process.exit(1)
}

const profile = await mkdtemp(path.join(tmpdir(), "threadly-preview-"))
const context = await chromium.launchPersistentContext(profile, {
  channel: "chromium",
  headless: false,
  viewport: null,
  args: [
    `--disable-extensions-except=${extension}`,
    `--load-extension=${extension}`,
    "--window-size=1320,900"
  ]
})

// A stand-in open email at a mail.google.com address, so the content script,
// the edge launcher and the real side panel work as they do on Gmail. The page
// is served locally by this script; no request reaches Google.
const gmailUrl = "https://mail.google.com/mail/u/0/#inbox/FMfcgzPreviewThreadDef456"
const gmailPage = await readFile(
  new URL("./preview-gmail.html", import.meta.url),
  "utf8"
)
await context.route("https://mail.google.com/**", (route) =>
  route.fulfill({ contentType: "text/html", body: gmailPage })
)

const jwt =
  "preview." +
  Buffer.from(
    JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 12 * 3600 })
  ).toString("base64url") +
  ".local"
let panel = null

// An extension reload (Chrome's reload button, or plasmo dev rebuilding)
// starts a new service worker and clears session storage, so re-seed each time.
const seeded = new WeakSet()
async function seed(worker) {
  // The startup worker can arrive both from the event and the explicit call.
  if (seeded.has(worker)) return
  seeded.add(worker)
  await worker.evaluate(
    async ({ origin, jwt, user }) => {
      await chrome.storage.local.set({ backendOrigin: origin })
      await chrome.storage.session.set({
        threadlySession: { jwt, user, origin }
      })
    },
    { origin, jwt, user }
  )
  const url = `chrome-extension://${worker.url().split("/")[2]}/sidepanel.html`
  if (!panel || panel.isClosed()) {
    panel = context.pages()[0] || (await context.newPage())
    await panel.goto(url)
  } else await panel.reload().catch(() => {})
}
context.on("serviceworker", (worker) =>
  seed(worker).catch((e) => console.warn("[preview] reseed failed:", e.message))
)
const worker =
  context.serviceWorkers()[0] || (await context.waitForEvent("serviceworker"))
await seed(worker)
const gmail = await context.newPage()
await gmail.goto(gmailUrl)
await gmail.bringToFront()

console.log(`
Threadly offline preview
  mock backend  ${origin}  (fixture data only)
  extension     ${path.relative(process.cwd(), extension)}${dev ? "  (live reload)" : ""}
  signed in as  ${user.email}  (synthetic local session)

Scripted prompts the fixture understands:
  hey                                  plain chat reply
  Show me all GYG emails               inbox cards, flight card, "show more"
  Find an email about GYG              the same search, from the "Find an email" shortcut
  (then click a card to attach it)
  Summarise this thread.               summary result
  Make it shorter                      refinement of the active task
  How much did I pay?                  answer card
  Draft a reply to this thread.        clarification, then editable draft
  Write an email thanking Alex …       recipient question; answer alex@example.test
  Find a meeting time tomorrow.        multi-step proposal and slot picker
  Menu (top left) → Recent work, Settings, appearance, Sign out

Two tabs are open:
  Test receipt           a stand-in open email. Click the Threadly button on the
                         right edge (or the toolbar icon) to open the real side
                         panel with that email attached.
  Threadly side panel    the panel on its own, with no email open.

Close the browser window or press Ctrl+C to stop.
`)

let stopping = false
async function stop() {
  if (stopping) return
  stopping = true
  plasmo?.kill()
  await context.close().catch(() => {})
  await new Promise((r) => server.close(() => r()))
  // Chromium can still be flushing its profile as it exits; retry, then give up quietly.
  await rm(profile, {
    recursive: true,
    force: true,
    maxRetries: 5,
    retryDelay: 200
  }).catch(() => {})
  process.exit(0)
}
context.on("close", stop)
process.on("SIGINT", stop)
process.on("SIGTERM", stop)
