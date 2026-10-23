import { mkdtemp, readFile, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import {
  chromium,
  expect,
  test,
  type BrowserContext,
  type Page
} from "@playwright/test"

import { categoryDisplay, type Category } from "../../lib/classification"

const categories = Object.keys(categoryDisplay) as Category[]
const snippet =
  " — A longer synthetic preview describing the next steps and background in enough detail to make an inbox row feel crowded. All of this original text must remain available."
const subjects = categories.map((_, i) =>
  i === 6
    ? "Project handover: review the checklist and confirm the next milestone"
    : `Review request ${i + 1}`
)
const fixture = (
  nested: boolean
) => `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;background:#fff;color:#202124;font:14px Arial,sans-serif}table{width:100%;table-layout:fixed;border-collapse:collapse}tr{height:44px;border-bottom:1px solid #e5e7eb}td{padding:0 6px;white-space:nowrap;overflow:hidden}.xS{overflow:hidden}.xT{display:flex;align-items:center;overflow:hidden}.y6{flex:0 1 auto;min-width:0;overflow:hidden;white-space:nowrap}.y2{color:#687078;flex:1 1 auto;white-space:nowrap}.bog{font-weight:600}.sender{overflow:hidden;text-overflow:ellipsis}.when{text-align:right;font-size:12px}button{border:0;background:none;font:inherit}.native-action{width:24px;height:24px}.account{height:24px;font-size:11px;color:#687078}@media(max-width:800px){col.sender-col{width:100px}}
</style></head><body><div class="account" aria-label="Google Account: Synthetic (tester@example.test)">Synthetic Gmail fixture</div><div role="main"><table><colgroup><col style="width:34px"><col style="width:34px"><col class="sender-col" style="width:156px"><col><col style="width:68px"></colgroup><tbody>
${categories
  .map((_, i) => {
    const subject = `<span class="bog" data-legacy-thread-id="d${i + 1}" data-legacy-last-message-id="d${i + 1}0">${subjects[i]}</span>`
    const preview = `<span class="y2"><span>${snippet}</span></span>`
    return `<tr class="zA" tabindex="0" data-i="${i}" aria-label="Synthetic thread ${i + 1}"><td><input type="checkbox" aria-label="Select synthetic thread ${i + 1}"></td><td><button class="native-action" aria-label="Star synthetic thread ${i + 1}">☆</button></td><td class="sender">Example sender ${i + 1}</td><td class="xY a4W"><div class="xS"><div class="xT">${nested ? `<div class="y6">${subject}${preview}</div>` : `<div class="y6">${subject}</div>${preview}`}</div></div></td><td class="when">12:13</td></tr>`
  })
  .join(
    ""
  )}</tbody></table></div><script>document.querySelector('table').addEventListener('click', e => { if(!e.target.closest('button,input')) document.body.dataset.opened = e.target.closest('tr')?.dataset.i || ''; });</script></body></html>`

let context: BrowserContext, page: Page, setup: Page, profile: string
test.beforeAll(async () => {
  profile = await mkdtemp(path.join(tmpdir(), "threadly-row-layout-"))
  const extension = path.resolve(
    process.env.THREADLY_E2E_EXTENSION_PATH || "build/chrome-mv3-prod"
  )
  await readFile(path.join(extension, "manifest.json"), "utf8")
  context = await chromium.launchPersistentContext(profile, {
    channel: "chromium",
    headless: true,
    timezoneId: "Australia/Melbourne",
    args: [
      `--disable-extensions-except=${extension}`,
      `--load-extension=${extension}`
    ]
  })
  const worker =
    context.serviceWorkers()[0] || (await context.waitForEvent("serviceworker"))
  setup = await context.newPage()
  await setup.goto(
    `chrome-extension://${new URL(worker.url()).host}/sidepanel.html`
  )
  const status = await setup.evaluate(() =>
    chrome.runtime.sendMessage({ channel: "threadly", type: "STATUS" })
  )
  await setup.evaluate(
    async ({ origin, categories }) => {
      const jwt =
        "header." +
        btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })) +
        ".signature"
      await chrome.storage.local.set({
        threadlySession: {
          jwt,
          refresh_token: "synthetic-refresh",
          user: { id: 1, email: "tester@example.test", name: "Synthetic" },
          origin
        }
      })
      const values = Object.fromEntries(
        categories.map((category, i) => {
          const thread = `d${i + 1}`
          return [
            `classification:1:${thread}:${thread}0:Australia/Melbourne`,
            {
              schema_version: "email-classification-with-action.v1",
              thread_id: thread,
              status: "classified",
              labels: {
                priority: i % 2 ? "Medium" : "High",
                needs_reply: i % 2 === 0,
                action: i % 2 ? "no_action" : "reply",
                category
              },
              valid_until: new Date(Date.now() + 3600000).toISOString()
            }
          ]
        })
      )
      await chrome.storage.session.set(values)
    },
    { origin: status.data.origin, categories }
  )
  // Cache fixtures satisfy every classification; no real API/model is used.
  await context.route("https://api.threadly.au/**", (route) => route.abort())
  page = await context.newPage()
})
test.afterAll(async () => {
  await context?.close()
  await rm(profile, { recursive: true, force: true })
})

for (const nested of [false, true])
  test(`${nested ? "nested" : "sibling"} Gmail snippets shorten while subjects, labels and row actions stay intact`, async () => {
    for (const width of [1440, 760, 640]) {
      await page.setViewportSize({ width, height: 720 })
      await context.unroute("https://mail.google.com/**")
      await context.route("https://mail.google.com/**", (route) =>
        route.fulfill({ contentType: "text/html", body: fixture(nested) })
      )
      await setup.evaluate(() =>
        chrome.storage.local.set({
          threadlyInboxMarks: { enabled: true, words: true }
        })
      )
      await page.goto("https://mail.google.com/mail/u/0/#inbox")
      await expect(page.locator(".tl-right[aria-label]")).toHaveCount(7)
      expect(
        await page
          .locator(".tl-right")
          .first()
          .evaluate((el) => getComputedStyle(el).cursor)
      ).toBe("default")
      const subjectStarts: number[] = []
      for (let i = 0; i < categories.length; i++) {
        const row = page.locator("tr.zA").nth(i)
        await expect(row.locator(".bog")).toHaveText(subjects[i])
        await expect(row.locator(".y2")).toHaveText(snippet)
        await expect(row.locator(".tl-right .tl-word")).toHaveText(
          categoryDisplay[categories[i]]
        )
        await expect(row.locator(".tl-right .tl-word")).toBeVisible()
        await expect(row.locator(".tl-left .tl-word")).toHaveText(
          i % 2 ? "No reply" : "Reply"
        )
        const geometry = await row.evaluate((el) => {
          const preview = el.querySelector<HTMLElement>(".y2")!
          const subject = el.querySelector<HTMLElement>(".bog")!
          return {
            previewWidth: preview.getBoundingClientRect().width,
            max: parseFloat(getComputedStyle(preview).maxInlineSize),
            clipped: preview.scrollWidth > preview.clientWidth,
            subjectX: subject.getBoundingClientRect().x,
            snippetGap:
              preview.getBoundingClientRect().left -
              subject.getBoundingClientRect().right,
            rowHeight: el.getBoundingClientRect().height
          }
        })
        expect(geometry.max).toBeLessThanOrEqual(
          width === 1440 ? 200 : width === 760 ? 130 : 70
        )
        expect(geometry.clipped).toBe(true)
        expect(geometry.previewWidth).toBeLessThanOrEqual(geometry.max + 1)
        expect(geometry.rowHeight).toBe(44)
        if (i < 6) expect(geometry.snippetGap).toBeGreaterThanOrEqual(3)
        subjectStarts.push(geometry.subjectX)
      }
      expect(
        Math.max(...subjectStarts) - Math.min(...subjectStarts)
      ).toBeLessThan(1)
      const nativeBefore = await page
        .locator("tr.zA")
        .first()
        .evaluate((el) => [
          el.querySelector("input")!.getBoundingClientRect().x,
          el.querySelector("button")!.getBoundingClientRect().x,
          el.querySelector(".when")!.getBoundingClientRect().x
        ])
      await page.screenshot({
        path: `test-results/inbox-snippets-${nested ? "nested" : "sibling"}-${width}-words.png`
      })
      await setup.evaluate(() =>
        chrome.storage.local.set({
          threadlyInboxMarks: { enabled: true, words: false }
        })
      )
      await expect(page.locator("html")).toHaveAttribute(
        "data-threadly-mark-words",
        "false"
      )
      const startsInIcons = await page
        .locator(".bog")
        .evaluateAll((els) => els.map((el) => el.getBoundingClientRect().x))
      expect(startsInIcons).toEqual(subjectStarts)
      expect(
        await page
          .locator(".tl-word")
          .first()
          .evaluate((el) => getComputedStyle(el).visibility)
      ).toBe("hidden")
      await page
        .getByRole("checkbox", { name: "Select synthetic thread 1" })
        .check()
      await expect(
        page.getByRole("checkbox", { name: "Select synthetic thread 1" })
      ).toBeChecked()
      await page
        .getByRole("button", { name: "Star synthetic thread 1" })
        .click()
      const nativeAfter = await page
        .locator("tr.zA")
        .first()
        .evaluate((el) => [
          el.querySelector("input")!.getBoundingClientRect().x,
          el.querySelector("button")!.getBoundingClientRect().x,
          el.querySelector(".when")!.getBoundingClientRect().x
        ])
      expect(nativeAfter).toEqual(nativeBefore)
      await page.locator(".bog").first().click()
      await expect(page.locator("body")).toHaveAttribute("data-opened", "0")
      await setup.evaluate(() =>
        chrome.storage.local.set({
          threadlyInboxMarks: { enabled: false, words: false }
        })
      )
      await expect(page.locator("html")).toHaveAttribute(
        "data-threadly-inbox-compact",
        "off"
      )
      expect(
        await page
          .locator(".y2")
          .first()
          .evaluate((el) => getComputedStyle(el).maxInlineSize)
      ).toBe("none")
      await expect(page.locator(".tl-slot:visible")).toHaveCount(0)
    }
  })
