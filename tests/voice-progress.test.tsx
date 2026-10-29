import { act, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { VoiceOrb } from "../components/VoiceOrb"

let recognizer: any
const fixtureChrome = globalThis.chrome
const utterances: any[] = []
let holdProgress = false
beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal("chrome", fixtureChrome)
  utterances.length = 0
  holdProgress = false
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null)
  vi.stubGlobal(
    "webkitSpeechRecognition",
    class {
      onresult: any
      onend: any
      onerror: any
      start() {}
      stop() {}
      abort() {}
      constructor() {
        recognizer = this
      }
    }
  )
  vi.stubGlobal(
    "SpeechSynthesisUtterance",
    class {
      constructor(public text: string) {}
    }
  )
  vi.stubGlobal("speechSynthesis", {
    cancel: vi.fn(),
    getVoices: () => [],
    speak: (utterance: any) => {
      utterances.push(utterance)
      utterance.onstart?.()
      if (!(holdProgress && /working on|taking a little/.test(utterance.text)))
        setTimeout(() => utterance.onend?.(), 10)
    }
  })
  vi.mocked(chrome.runtime.sendMessage).mockResolvedValue({
    ok: false,
    error: {
      status: 502,
      code: "voice_provider_error",
      message: "Synthetic fallback"
    }
  } as any)
})
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})
const say = (text: string) =>
  act(() => recognizer.onresult({ results: [[{ transcript: text }]] }))
const advance = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms))

it.each([
  "Hey Threadly, how are you?",
  "How's it going?",
  "I'm good, thanks",
  "Thanks Threadly",
  "Yes",
  "Cancel, cancel",
  "Stop working on that",
  "Actually, use tomorrow"
])(
  "does not fill a pending social/control turn with task audio: %s",
  async (text) => {
    let resolve!: (value: string) => void
    const respond = vi.fn(() => new Promise<string>((r) => (resolve = r)))
    render(<VoiceOrb respond={respond} onClose={vi.fn()} />)
    say(text)
    await advance(10000)
    expect(respond).toHaveBeenCalledWith(text)
    expect(utterances).toEqual([])
    await act(async () => resolve("A direct answer."))
    await advance(20)
    expect(utterances.map((u) => u.text)).toEqual(["A direct answer."])
  }
)

it("speaks a fast tool result directly without a preamble or false completion", async () => {
  render(
    <VoiceOrb
      respond={vi
        .fn()
        .mockResolvedValue("Your draft is ready to review in the chat.")}
      onClose={vi.fn()}
    />
  )
  say("Draft a reply to this email")
  await advance(5000)
  expect(utterances.map((u) => u.text)).toEqual([
    "Your draft is ready to review in the chat."
  ])
})

it("gives one delayed truthful cue and lets the ready answer interrupt a stuck cue", async () => {
  holdProgress = true
  let resolve!: (value: string) => void
  render(
    <VoiceOrb
      respond={() => new Promise<string>((r) => (resolve = r))}
      onClose={vi.fn()}
    />
  )
  say("Check my latest email")
  await advance(3600)
  expect(utterances).toEqual([])
  await advance(150)
  expect(utterances.map((u) => u.text)).toEqual([
    "I'm still working on your request."
  ])
  await advance(10000)
  expect(utterances).toHaveLength(1)
  await act(async () =>
    resolve("I couldn't read that email. Please select it again.")
  )
  await advance(20)
  expect(utterances.map((u) => u.text)).toEqual([
    "I'm still working on your request.",
    "I couldn't read that email. Please select it again."
  ])
  expect(screen.getByRole("status").textContent).toBe("Listening…")
  // Cancel emits no browser end event here. Production cleanup must settle the
  // old utterance itself and prevent a late callback restarting its level timer.
  act(() => utterances[0].onstart?.())
  expect(vi.getTimerCount()).toBeLessThan(4)
})

it("varies slow progress across turns without implying a save/send succeeded", async () => {
  const pending: ((value: string) => void)[] = []
  render(
    <VoiceOrb
      respond={() => new Promise<string>((r) => pending.push(r))}
      onClose={vi.fn()}
    />
  )
  for (const request of ["Check my inbox", "Draft a reply"]) {
    say(request)
    await advance(3800)
    await act(async () => pending.at(-1)!("Ready for review."))
    await advance(20)
  }
  expect(utterances.map((u) => u.text)).toEqual([
    "I'm still working on your request.",
    "Ready for review.",
    "This is taking a little longer.",
    "Ready for review."
  ])
})

it("does not hold a ready answer behind a pending progress-speech provider request", async () => {
  let releaseAudio!: (value: any) => void
  vi.mocked(chrome.runtime.sendMessage).mockImplementation((async (
    message: any
  ) =>
    message.body.text === "I'm still working on your request."
      ? new Promise((resolve) => (releaseAudio = resolve))
      : {
          ok: false,
          error: {
            code: "voice_provider_error",
            message: "Synthetic fallback",
            status: 502
          }
        }) as any)
  let resolve!: (value: string) => void
  render(
    <VoiceOrb
      respond={() => new Promise<string>((r) => (resolve = r))}
      onClose={vi.fn()}
    />
  )
  say("Draft a reply")
  await advance(3800)
  expect(releaseAudio).toBeTypeOf("function")
  expect(utterances).toHaveLength(0)
  await act(async () => resolve("Your reply is ready to review."))
  await advance(20)
  expect(utterances.map((u) => u.text)).toEqual([
    "Your reply is ready to review."
  ])
  await act(async () => releaseAudio({ ok: true, data: { audio: "" } }))
  expect(utterances).toHaveLength(1)
  expect(screen.getByRole("status").textContent).toBe("Listening…")
})

it("clears pending cues on close and ignores an old response after rapid reopen", async () => {
  let resolve!: (value: string) => void
  const first = render(
    <VoiceOrb
      respond={() => new Promise<string>((r) => (resolve = r))}
      onClose={vi.fn()}
    />
  )
  say("Find my latest email")
  await advance(1500)
  fireEvent.click(
    screen.getByRole("button", { name: "Close voice conversation" })
  )
  first.unmount()
  render(
    <VoiceOrb
      respond={vi.fn().mockResolvedValue("Hey! How are you? ")}
      onClose={vi.fn()}
    />
  )
  say("Hey Threadly how are you")
  await advance(5000)
  await act(async () => resolve("Old answer must stay quiet"))
  expect(utterances.map((u) => u.text)).toEqual(["Hey! How are you? "])
})
