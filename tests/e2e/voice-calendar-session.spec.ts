import { mkdtemp, rm } from "node:fs/promises"
import { createServer, type Server } from "node:http"
import { tmpdir } from "node:os"
import path from "node:path"
import {
  chromium,
  expect,
  test,
  type BrowserContext,
  type Page
} from "@playwright/test"

import { createMockBackend, user, type MockBackend } from "./mock-backend"

// These are synthetic provider/conversation responses, never real Calendar writes.
// Backend test_voice.py separately verifies the upstream -> public error mapping.
const request = "Create me an event at 3 p.m. tomorrow"
const question = "What should I call the event?"
const answer = "Focus is ready to review in the chat."
const event = {
  action_id: "voice-event",
  state: "proposed",
  version: 1,
  payload_hash: "synthetic-event-hash",
  blockers: [],
  approval_available: true,
  authorization: "separate_exact_event_approval",
  preview: {
    calendar_id: "primary",
    calendar_name: "Personal calendar",
    send_updates: "none",
    event: {
      summary: "Focus",
      attendees: [],
      location: "",
      description: "",
      start: { dateTime: "2030-10-08T15:00:00Z", timeZone: "UTC" },
      end: { dateTime: "2030-10-08T15:30:00Z", timeZone: "UTC" }
    }
  }
}
let backend: MockBackend, server: Server, context: BrowserContext, page: Page
let profile: string, origin: string
let turns: any[], voiceCalls: string[], failure: number, taskQuestion: boolean
let holdSpeech: boolean, releaseSpeech: () => void
const errors: string[] = []

// A short silent WAV exercises real decoding/playback without an external voice.
const wav = Buffer.alloc(44 + 800)
wav.write("RIFF", 0)
wav.writeUInt32LE(wav.length - 8, 4)
wav.write("WAVEfmt ", 8)
wav.writeUInt32LE(16, 16)
wav.writeUInt16LE(1, 20)
wav.writeUInt16LE(1, 22)
wav.writeUInt32LE(8000, 24)
wav.writeUInt32LE(16000, 28)
wav.writeUInt16LE(2, 32)
wav.writeUInt16LE(16, 34)
wav.write("data", 36)
wav.writeUInt32LE(800, 40)

test.beforeEach(async () => {
  backend = createMockBackend()
  turns = []
  voiceCalls = []
  failure = 502
  taskQuestion = false
  holdSpeech = false
  releaseSpeech = () => {}
  errors.length = 0
  server = createServer(async (req, res) => {
    if (
      req.url === "/assistant/calendar-actions/voice-event" &&
      req.method === "GET"
    ) {
      res.setHeader("Content-Type", "application/json")
      res.end(JSON.stringify(event))
      return
    }
    if (!["/voice/speak", "/assistant/conversation-turns"].includes(req.url!)) {
      backend.server.emit("request", req, res)
      return
    }
    let raw = ""
    for await (const chunk of req) raw += chunk
    const body = JSON.parse(raw)
    res.setHeader("Content-Type", "application/json")
    res.setHeader("Access-Control-Allow-Origin", "*")
    if (req.url === "/voice/speak") {
      voiceCalls.push(body.text)
      if (body.text === answer) {
        if (holdSpeech)
          await new Promise<void>((resolve) => {
            releaseSpeech = resolve
          })
        res.statusCode = failure
        res.end(
          JSON.stringify({
            error: {
              code: failure === 401 ? "unauthorized" : "voice_provider_error",
              message: "Speech is unavailable right now. Try again later."
            }
          })
        )
      } else res.end(JSON.stringify({ audio: wav.toString("base64") }))
      return
    }
    turns.push(body)
    res.end(
      JSON.stringify({
        conversation_id: body.conversation_id,
        version: body.expected_version + 1,
        kind:
          turns.length === 2
            ? "calendar_event"
            : turns.length === 1 && taskQuestion
              ? "task"
              : "message",
        ...(turns.length === 2
          ? { calendar_action_id: event.action_id, calendar_action: event }
          : {}),
        text:
          turns.length === 1
            ? question
            : turns.length === 2
              ? answer
              : "You're welcome.",
        ...(turns.length === 1 && taskQuestion
          ? {
              task: {
                task_id: "voice-calendar-task",
                state: "needs_clarification",
                version: 1,
                instruction: request,
                artifact_id: null,
                question: {
                  question_id: "title-question",
                  prompt: question,
                  fields: [],
                  expected_version: 1
                }
              }
            }
          : {})
      })
    )
  })
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r))
  origin = `http://127.0.0.1:${(server.address() as any).port}`
  profile = await mkdtemp(path.join(tmpdir(), "threadly-voice-calendar-"))
  const extension = path.resolve("build/chrome-mv3-prod")
  context = await chromium.launchPersistentContext(profile, {
    channel: "chromium",
    headless: true,
    args: [
      `--disable-extensions-except=${extension}`,
      `--load-extension=${extension}`
    ]
  })
  const worker =
    context.serviceWorkers()[0] || (await context.waitForEvent("serviceworker"))
  await worker.evaluate(
    async ({ origin, user }) => {
      await chrome.storage.local.set({
        backendOrigin: origin,
        threadlySession: {
          origin,
          user,
          refresh_token: "synthetic-refresh",
          jwt:
            "fixture." +
            btoa(
              JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })
            ) +
            ".access"
        }
      })
    },
    { origin, user }
  )
  page = await context.newPage()
  page.on("pageerror", (e) => errors.push(e.message))
  await page.addInitScript(() => {
    const state = ((window as any).voiceTest = {
      recognizer: null as any,
      spoken: [] as string[],
      rejections: [] as string[]
    })
    window.addEventListener("unhandledrejection", (e) =>
      state.rejections.push(String(e.reason))
    )
    class Recognition {
      onresult: any
      onend: any
      onerror: any
      constructor() {
        state.recognizer = this
      }
      start() {}
      stop() {
        queueMicrotask(() => this.onend?.())
      }
      abort() {}
    }
    ;(window as any).SpeechRecognition = Recognition
    // No actual microphone, network recognition, or audible browser speech.
    navigator.mediaDevices.getUserMedia = async () => {
      throw new Error("fixture mic disabled")
    }
    Object.defineProperty(window, "speechSynthesis", {
      value: {
        getVoices: () => [],
        cancel: () => {},
        speak: (u: SpeechSynthesisUtterance) => {
          state.spoken.push(u.text)
          setTimeout(
            () => u.onend?.(new Event("end") as SpeechSynthesisEvent),
            10
          )
        }
      }
    })
  })
  await page.goto(
    `chrome-extension://${new URL(worker.url()).host}/sidepanel.html`
  )
  await expect(
    page.getByRole("button", { name: "Talk to Threadly" })
  ).toBeEnabled()
})

test.afterEach(async () => {
  releaseSpeech?.()
  expect(errors).toEqual([])
  expect(
    await page.evaluate(() => (window as any).voiceTest.rejections)
  ).toEqual([])
  // Neither the worker nor the conversation invokes logout/reconnect/disconnect,
  // approval, draft-save, or a real provider action in this test.
  expect(
    backend.calls.filter((c) => /\/auth\/|\/approve|\/drafts/.test(c.path))
  ).toEqual([])
  await context.close()
  await new Promise<void>((r) => server.close(() => r()))
  await rm(profile, { recursive: true, force: true })
})

const stored = () =>
  page.evaluate(
    async () =>
      (await chrome.storage.local.get("threadlySession")).threadlySession
  )
async function say(text: string) {
  await expect(page.locator(".voice-status")).toHaveText("Listening…")
  await page.evaluate(
    (text) =>
      (window as any).voiceTest.recognizer.onresult({
        results: [[{ transcript: text }]]
      }),
    text
  )
}
async function firstTurn() {
  await page.getByRole("button", { name: "Talk to Threadly" }).click()
  await say(request)
  await expect.poll(() => voiceCalls.includes(question)).toBe(true)
  await expect(page.locator(".voice-status")).toHaveText("Listening…")
}

test("legacy speech 401 reproduces local sign-out after the spoken title", async () => {
  failure = 401
  await firstTurn()
  await say("Focus")
  await expect(
    page.getByRole("button", { name: "Sign in with Google" })
  ).toBeVisible()
  expect(await stored()).toBeUndefined()
  expect(turns.map((t) => t.instruction)).toEqual([request, "Focus"])
})

for (const status of [502, 503, 504, 403, 429]) {
  test(`speech ${status} falls back and preserves the full voice conversation`, async () => {
    failure = status
    await firstTurn()
    await say("Focus")
    await expect
      .poll(() => page.evaluate(() => (window as any).voiceTest.spoken))
      .toContain(answer)
    await say("Thank you")
    await expect.poll(() => turns.length).toBe(3)
    await expect(page.locator(".voice-status")).toHaveText("Listening…")
    expect((await stored()).user.id).toBe(user.id)
    expect(new Set(turns.map((t) => t.conversation_id)).size).toBe(1)
    expect(turns.map((t) => t.expected_version)).toEqual([0, 1, 2])
    expect(turns.map((t) => t.instruction)).toEqual([
      request,
      "Focus",
      "Thank you"
    ])
    await page.getByRole("button", { name: "Close voice conversation" }).click()
    await expect(
      page.getByRole("region", { name: "Calendar event" })
    ).toContainText("Ready to review")
    await expect(
      page.getByRole("button", { name: "Create event", exact: true })
    ).toBeEnabled()
  })
}

test("typed clarification never requests speech and keeps the same session", async () => {
  for (const text of [request, "Focus", "Thank you"]) {
    await page.getByLabel("Your request").fill(text)
    await page.getByLabel("Your request").press("Enter")
    await expect.poll(() => turns.at(-1)?.instruction).toBe(text)
    await expect(
      page.getByRole("button", { name: "Talk to Threadly" })
    ).toBeEnabled()
  }
  expect(voiceCalls).toEqual([])
  expect((await stored()).user.id).toBe(user.id)
  expect(turns.map((t) => t.expected_version)).toEqual([0, 1, 2])
  await expect(
    page.getByRole("region", { name: "Calendar event" })
  ).toContainText("Ready to review")
})

test("later voice turns carry the task created while the orb was already open", async () => {
  taskQuestion = true
  await firstTurn()
  await say("Focus")
  await expect.poll(() => turns.length).toBe(2)
  expect(turns[1].active_task_id).toBe("voice-calendar-task")
  await expect(page.locator(".voice-status")).toHaveText("Listening…")
})

test("Close during pending speech does not reset auth or speak a stale answer after reopen", async () => {
  holdSpeech = true
  await firstTurn()
  await say("Focus")
  await expect.poll(() => voiceCalls.includes(answer)).toBe(true)
  await page.getByRole("button", { name: "Close voice conversation" }).click()
  await page.getByRole("button", { name: "Talk to Threadly" }).click()
  releaseSpeech()
  await say("Thank you")
  await expect.poll(() => turns.length).toBe(3)
  await expect(page.locator(".voice-status")).toHaveText("Listening…")
  expect(
    await page.evaluate(() => (window as any).voiceTest.spoken)
  ).not.toContain(answer)
  expect((await stored()).user.id).toBe(user.id)
})
