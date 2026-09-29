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
import { mkdtemp, rm } from "node:fs/promises"
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
    "--window-size=440,960"
  ]
})

const jwt =
  "preview." +
  Buffer.from(
    JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 12 * 3600 })
  ).toString("base64url") +
  ".local"
let panel = null

// Preview-only design controls: a thin strip above the panel for switching
// appearance and trying dark tones. Injected from here, never part of the build.
// Add CSS-variable overrides here to try alternative dark tones in the preview.
const tones = {}
const look = { scheme: "dark", tone: null }
async function applyLook(page) {
  await page.emulateMedia({ colorScheme: look.scheme })
  await page.evaluate(
    ({ look, tones }) => {
      document.getElementById("tl-preview-bar")?.remove()
      document.getElementById("tl-preview-tone")?.remove()
      const vars = tones[look.tone]
      const tone = document.createElement("style")
      tone.id = "tl-preview-tone"
      tone.textContent =
        "body{padding-top:34px}.threadly{height:calc(100dvh - 34px)!important}" +
        (vars
          ? ".threadly.dark{" +
            Object.entries(vars).map(([k, v]) => `${k}:${v}`).join(";") +
            "}"
          : "")
      document.head.append(tone)
      const bar = document.createElement("div")
      bar.id = "tl-preview-bar"
      bar.style.cssText =
        "position:fixed;inset:0 0 auto 0;height:34px;z-index:99;display:flex;" +
        "align-items:center;gap:4px;padding:0 8px;background:#111;color:#bbb;" +
        "font:500 11px -apple-system,sans-serif;overflow-x:auto;white-space:nowrap"
      const chip = (label, active, onClick) => {
        const b = document.createElement("button")
        b.textContent = label
        b.style.cssText =
          "all:unset;cursor:pointer;padding:4px 8px;border-radius:5px;" +
          (active ? "background:#fff;color:#111" : "color:#bbb")
        b.onclick = onClick
        return b
      }
      const label = document.createElement("span")
      label.textContent = "PREVIEW"
      label.style.cssText = "color:#d9a53f;letter-spacing:.08em;margin-right:6px"
      bar.append(label)
      for (const scheme of ["light", "dark"])
        bar.append(
          chip(scheme, look.scheme === scheme, () =>
            window.__threadlyPreview({ scheme })
          )
        )
      for (const tone of Object.keys(tones))
        bar.append(
          chip(tone, look.tone === tone, () =>
            window.__threadlyPreview({ scheme: "dark", tone })
          )
        )
      document.body.append(bar)
    },
    { look, tones }
  )
}
// An extension reload (Chrome's reload button, or plasmo dev rebuilding)
// starts a new service worker and clears session storage, so re-seed each time.
async function seed(worker) {
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
    await panel.exposeBinding("__threadlyPreview", async (_, change) => {
      Object.assign(look, change)
      await applyLook(panel)
    })
    // The panel has its own appearance toggle; enable this to preview system-theme designs.
    if (process.env.THREADLY_PREVIEW_BAR)
      panel.on("load", () => applyLook(panel).catch(() => {}))
    await panel.goto(url)
  } else await panel.reload()
}
context.on("serviceworker", (worker) =>
  seed(worker).catch((e) => console.warn("[preview] reseed failed:", e.message))
)
const worker =
  context.serviceWorkers()[0] || (await context.waitForEvent("serviceworker"))
await seed(worker)

console.log(`
Threadly offline preview
  mock backend  ${origin}  (fixture data only)
  extension     ${path.relative(process.cwd(), extension)}${dev ? "  (live reload)" : ""}
  signed in as  ${user.email}  (synthetic local session)

Scripted prompts the fixture understands:
  hey                                  plain chat reply
  Show me all GYG emails               inbox cards, flight card, "show more"
  (then click a card to attach it)
  Summarise this thread.               summary result
  Make it shorter                      refinement of the active task
  How much did I pay?                  answer card
  Draft a reply to this thread.        clarification, then editable draft
  Write an email thanking Alex …       recipient question; answer alex@example.test
  Find a meeting time tomorrow.        multi-step proposal and slot picker
  Menu (top left) → Recent work, Settings, appearance, Sign out

Close the browser window or press Ctrl+C to stop.
`)

let stopping = false
async function stop() {
  if (stopping) return
  stopping = true
  plasmo?.kill()
  await context.close().catch(() => {})
  await new Promise((r) => server.close(() => r()))
  await rm(profile, { recursive: true, force: true })
  process.exit(0)
}
context.on("close", stop)
process.on("SIGINT", stop)
process.on("SIGTERM", stop)
