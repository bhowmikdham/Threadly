import { mkdtemp, rm } from "node:fs/promises"
import type { Server } from "node:http"
import { tmpdir } from "node:os"
import path from "node:path"
import {
  chromium,
  expect,
  test,
  type BrowserContext,
  type Page
} from "@playwright/test"

import { createMockBackend, user } from "./mock-backend"

let server: Server, context: BrowserContext, page: Page, profile: string
const errors: string[] = []
test.beforeAll(async () => {
  ;({ server } = createMockBackend())
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve))
  const origin = `http://127.0.0.1:${(server.address() as any).port}`
  profile = await mkdtemp(path.join(tmpdir(), "threadly-voice-lifecycle-"))
  const extension = path.resolve("build/chrome-mv3-prod")
  context = await chromium.launchPersistentContext(profile, {
    channel: "chromium",
    headless: true,
    args: [
      `--disable-extensions-except=${extension}`,
      `--load-extension=${extension}`,
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream"
    ]
  })
  const worker =
    context.serviceWorkers()[0] || (await context.waitForEvent("serviceworker"))
  const jwt =
    "voice-fixture." +
    Buffer.from(
      JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })
    ).toString("base64url") +
    ".local"
  await worker.evaluate(
    async ({ origin, jwt, user }) => {
      await chrome.storage.local.set({
        backendOrigin: origin,
        threadlySession: { jwt, user, origin }
      })
    },
    { origin, jwt, user }
  )
  page = await context.newPage()
  page.on("pageerror", (error) => errors.push(error.message))
  await page.addInitScript(() => {
    const diagnostics = ((window as any).voiceDiagnostics = {
      contexts: [] as { context: AudioContext; closes: number }[],
      streams: [] as MediaStream[],
      rejections: [] as string[],
      holdMicrophone: false,
      releaseMicrophone: () => {}
    })
    window.addEventListener("unhandledrejection", (event) =>
      diagnostics.rejections.push(String(event.reason))
    )
    class Recognition {
      onresult: any
      onerror: any
      onend: any
      start() {}
      stop() {
        this.onend?.()
      }
      abort() {
        this.onend?.()
      }
    }
    ;(window as any).SpeechRecognition = Recognition
    const Audio = window.AudioContext
    window.AudioContext = class extends Audio {
      constructor(options?: AudioContextOptions) {
        super(options)
        const record = { context: this, closes: 0 }
        diagnostics.contexts.push(record)
        const close = this.close.bind(this)
        this.close = () => {
          record.closes++
          return close()
        }
      }
    }
    const getUserMedia = navigator.mediaDevices.getUserMedia.bind(
      navigator.mediaDevices
    )
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      const stream = await getUserMedia(constraints)
      diagnostics.streams.push(stream)
      if (diagnostics.holdMicrophone)
        await new Promise<void>((resolve) => {
          diagnostics.releaseMicrophone = resolve
        })
      return stream
    }
  })
  await page.goto(
    `chrome-extension://${new URL(worker.url()).host}/sidepanel.html`
  )
})
test.beforeEach(async () => {
  errors.length = 0
  await page.reload()
})
test.afterEach(async () => {
  expect(errors).toEqual([])
  expect(
    await page.evaluate(() => (window as any).voiceDiagnostics.rejections)
  ).toEqual([])
})
test.afterAll(async () => {
  await context?.close()
  await new Promise<void>((resolve) => server?.close(() => resolve()))
  if (profile) await rm(profile, { recursive: true, force: true })
})

test("native AudioContexts close once through repeated open, Close, Escape and unmount", async () => {
  for (let i = 0; i < 3; i++) {
    await page.getByRole("button", { name: "Talk to Threadly" }).click()
    await expect(
      page.getByRole("dialog", { name: "Voice conversation" })
    ).toBeVisible()
    await expect
      .poll(() =>
        page.evaluate(() => (window as any).voiceDiagnostics.contexts.length)
      )
      .toBe(i + 1)
    if (i === 1) await page.keyboard.press("Escape")
    else
      await page
        .getByRole("button", { name: "Close voice conversation" })
        .click()
    await expect(
      page.getByRole("dialog", { name: "Voice conversation" })
    ).toHaveCount(0)
  }
  await expect
    .poll(() =>
      page.evaluate(() =>
        (window as any).voiceDiagnostics.contexts.map((record: any) => ({
          state: record.context.state,
          closes: record.closes
        }))
      )
    )
    .toEqual([
      { state: "closed", closes: 1 },
      { state: "closed", closes: 1 },
      { state: "closed", closes: 1 }
    ])
  expect(
    await page.evaluate(() =>
      (window as any).voiceDiagnostics.streams.flatMap((stream: MediaStream) =>
        stream.getTracks().map((track) => track.readyState)
      )
    )
  ).toEqual(["ended", "ended", "ended"])
})

test("a delayed microphone is stopped after Close and rapid reopen still works", async () => {
  await page.evaluate(() => {
    ;(window as any).voiceDiagnostics.holdMicrophone = true
  })
  await page.getByRole("button", { name: "Talk to Threadly" }).click()
  await expect
    .poll(() =>
      page.evaluate(() => (window as any).voiceDiagnostics.streams.length)
    )
    .toBe(1)
  await page.getByRole("button", { name: "Close voice conversation" }).click()
  await page.evaluate(() => {
    const diagnostics = (window as any).voiceDiagnostics
    diagnostics.holdMicrophone = false
    diagnostics.releaseMicrophone()
  })
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as any).voiceDiagnostics.streams[0].getTracks()[0].readyState
      )
    )
    .toBe("ended")
  expect(
    await page.evaluate(() => (window as any).voiceDiagnostics.contexts.length)
  ).toBe(0)
  await page.getByRole("button", { name: "Talk to Threadly" }).click()
  await expect
    .poll(() =>
      page.evaluate(() => (window as any).voiceDiagnostics.contexts.length)
    )
    .toBe(1)
  await page.getByRole("button", { name: "Close voice conversation" }).click()
  await expect
    .poll(() =>
      page.evaluate(
        () => (window as any).voiceDiagnostics.contexts[0].context.state
      )
    )
    .toBe("closed")
  expect(
    await page.evaluate(
      () => (window as any).voiceDiagnostics.contexts[0].closes
    )
  ).toBe(1)
})
