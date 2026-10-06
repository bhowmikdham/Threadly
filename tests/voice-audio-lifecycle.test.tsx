import { act, fireEvent, render, screen } from "@testing-library/react"
import { StrictMode } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { VoiceOrb, type VoiceReply } from "../components/VoiceOrb"

const recognizers: FakeRecognition[] = []
class FakeRecognition {
  onresult: any
  onerror: any
  onend: any
  startedHandlers: any
  start = vi.fn(() => {
    this.startedHandlers = {
      result: this.onresult,
      error: this.onerror,
      end: this.onend
    }
  })
  stop = vi.fn()
  abort = vi.fn()
  constructor() {
    recognizers.push(this)
  }
}
const contexts: FakeAudioContext[] = []
class FakeAudioContext {
  state = "running"
  analyser = {
    fftSize: 512,
    getByteTimeDomainData: vi.fn((samples: Uint8Array) => samples.fill(128))
  }
  createAnalyser = vi.fn(() => this.analyser)
  createMediaStreamSource = vi.fn(() => ({ connect: vi.fn() }))
  close = vi.fn(() => {
    this.state = "closed"
    return Promise.resolve()
  })
  constructor() {
    contexts.push(this)
  }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}
function media() {
  const stop = vi.fn()
  return {
    stream: { getTracks: () => [{ stop }] } as unknown as MediaStream,
    stop
  }
}
const getUserMedia = vi.fn()
const speak = vi.fn()
const closeVoice = () =>
  fireEvent.click(
    screen.getByRole("button", { name: "Close voice conversation" })
  )
const flush = () =>
  act(async () => {
    await Promise.resolve()
  })

beforeEach(() => {
  vi.useFakeTimers()
  contexts.length = 0
  recognizers.length = 0
  getUserMedia.mockReset()
  getUserMedia.mockResolvedValue(media().stream)
  speak.mockReset()
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null)
  vi.stubGlobal("AudioContext", FakeAudioContext)
  vi.stubGlobal("webkitSpeechRecognition", FakeRecognition)
  vi.stubGlobal("speechSynthesis", {
    getVoices: () => [],
    cancel: vi.fn(),
    speak
  })
  vi.stubGlobal(
    "SpeechSynthesisUtterance",
    class {
      constructor(public text: string) {}
    }
  )
  vi.stubGlobal("navigator", {
    ...navigator,
    language: "en-AU",
    mediaDevices: { getUserMedia }
  })
})
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe("VoiceOrb audio resource lifecycle", () => {
  it("stops once when Close is repeated and unmount occurs before close settles", async () => {
    const microphone = media()
    getUserMedia.mockResolvedValue(microphone.stream)
    const onClose = vi.fn()
    const view = render(<VoiceOrb respond={vi.fn()} onClose={onClose} />)
    await flush()
    const closing = deferred<void>()
    contexts[0].close.mockImplementation(() => closing.promise)
    closeVoice()
    closeVoice()
    view.unmount()
    expect(contexts[0].close).toHaveBeenCalledOnce()
    expect(microphone.stop).toHaveBeenCalledOnce()
    expect(recognizers[0].abort).toHaveBeenCalledOnce()
    expect(onClose).toHaveBeenCalledOnce()
    const reads = contexts[0].analyser.getByteTimeDomainData.mock.calls.length
    await act(() => vi.advanceTimersByTimeAsync(100))
    expect(contexts[0].analyser.getByteTimeDomainData).toHaveBeenCalledTimes(
      reads
    )
    await act(async () => closing.resolve())
  })

  it("does not close an already closed context", async () => {
    const view = render(<VoiceOrb respond={vi.fn()} onClose={vi.fn()} />)
    await flush()
    contexts[0].state = "closed"
    closeVoice()
    view.unmount()
    expect(contexts[0].close).not.toHaveBeenCalled()
  })

  it("handles a rejected close without disturbing a rapidly reopened session", async () => {
    const first = render(<VoiceOrb respond={vi.fn()} onClose={vi.fn()} />)
    await flush()
    const closing = deferred<void>()
    // A plain function leaves rejection handling entirely to production code;
    // a mock's promise-result tracking could otherwise consume the rejection.
    Object.defineProperty(contexts[0], "close", {
      value: () => closing.promise
    })
    closeVoice()
    first.unmount()
    const secondClose = vi.fn()
    const second = render(<VoiceOrb respond={vi.fn()} onClose={secondClose} />)
    await flush()
    await act(async () =>
      closing.reject(
        new DOMException(
          "Cannot close a closed AudioContext.",
          "InvalidStateError"
        )
      )
    )
    expect(contexts[1].close).not.toHaveBeenCalled()
    expect(recognizers[1].start).toHaveBeenCalledOnce()
    expect(secondClose).not.toHaveBeenCalled()
    expect(screen.getByRole("status").textContent).toBe("Listening…")
    second.unmount()
  })

  it("stops a microphone obtained after unmount without creating an AudioContext", async () => {
    const permission = deferred<MediaStream>()
    getUserMedia.mockReturnValue(permission.promise)
    const view = render(<VoiceOrb respond={vi.fn()} onClose={vi.fn()} />)
    view.unmount()
    const microphone = media()
    await act(async () => permission.resolve(microphone.stream))
    expect(microphone.stop).toHaveBeenCalledOnce()
    expect(contexts).toHaveLength(0)
  })

  it("restarts under StrictMode and keeps the old pending microphone out of the new session", async () => {
    const firstPermission = deferred<MediaStream>()
    const secondPermission = deferred<MediaStream>()
    getUserMedia
      .mockReturnValueOnce(firstPermission.promise)
      .mockReturnValueOnce(secondPermission.promise)
    const onClose = vi.fn()
    const view = render(
      <StrictMode>
        <VoiceOrb respond={vi.fn()} onClose={onClose} />
      </StrictMode>
    )
    expect(recognizers).toHaveLength(2)
    expect(recognizers[0].abort).toHaveBeenCalledOnce()
    expect(recognizers[1].start).toHaveBeenCalledOnce()
    const stale = media(),
      active = media()
    await act(async () => secondPermission.resolve(active.stream))
    await act(async () => firstPermission.resolve(stale.stream))
    expect(stale.stop).toHaveBeenCalledOnce()
    expect(active.stop).not.toHaveBeenCalled()
    expect(contexts).toHaveLength(1)
    expect(contexts[0].createMediaStreamSource).toHaveBeenCalledWith(
      active.stream
    )
    act(() => {
      recognizers[0].startedHandlers.error({ error: "not-allowed" })
      recognizers[0].startedHandlers.end()
    })
    expect(onClose).not.toHaveBeenCalled()
    expect(recognizers[1].start).toHaveBeenCalledOnce()
    view.unmount()
    expect(active.stop).toHaveBeenCalledOnce()
    expect(contexts[0].close).toHaveBeenCalledOnce()
  })

  it("reports unavailable recognition only once during StrictMode setup", () => {
    vi.stubGlobal("webkitSpeechRecognition", undefined)
    const onClose = vi.fn()
    render(
      <StrictMode>
        <VoiceOrb respond={vi.fn()} onClose={onClose} />
      </StrictMode>
    )
    expect(onClose).toHaveBeenCalledOnce()
  })

  it.each<VoiceReply>([
    "An old answer",
    { text: "An old answer", showChat: true }
  ])(
    "ignores the old reply %j and recognition callbacks after rapid restart",
    async (reply) => {
      const answer = deferred<VoiceReply>()
      const firstClose = vi.fn()
      const first = render(
        <VoiceOrb respond={() => answer.promise} onClose={firstClose} />
      )
      await flush()
      const oldError = recognizers[0].onerror
      const oldResult = recognizers[0].onresult
      act(() => oldResult({ results: [[{ transcript: "Summarise this" }]] }))
      await act(() => vi.advanceTimersByTimeAsync(1300))
      expect(speak.mock.calls.map(([utterance]) => utterance.text)).toEqual([
        "Sure, I can do that."
      ])
      const acknowledgment = speak.mock.calls[0][0]
      const cancellations = vi.mocked(window.speechSynthesis.cancel).mock.calls
        .length
      first.unmount()
      expect(window.speechSynthesis.cancel).toHaveBeenCalledTimes(
        cancellations + 1
      )
      expect(contexts[0].close).toHaveBeenCalledOnce()
      expect(recognizers[0].abort).toHaveBeenCalledOnce()
      expect(recognizers[0].onresult).toBeNull()
      const spokenBeforeRestart = speak.mock.calls.length
      const respond = vi.fn()
      const second = render(<VoiceOrb respond={respond} onClose={vi.fn()} />)
      await flush()
      act(() => {
        oldError({ error: "not-allowed" })
        oldResult({ results: [[{ transcript: "Late result" }]] })
      })
      await act(async () => {
        // A cancelled browser acknowledgment may still emit a late completion.
        // Neither that callback nor the old response may speak in the new session.
        acknowledgment.onend()
        answer.resolve(reply)
      })
      await act(() => vi.advanceTimersByTimeAsync(1300))
      expect(speak).toHaveBeenCalledTimes(spokenBeforeRestart)
      expect(
        speak.mock.calls.some(
          ([utterance]) => utterance.text === "An old answer"
        )
      ).toBe(false)
      expect(firstClose).not.toHaveBeenCalled()
      expect(respond).not.toHaveBeenCalled()
      expect(contexts[1].close).not.toHaveBeenCalled()
      expect(recognizers[1].start).toHaveBeenCalledOnce()
      second.unmount()
    }
  )
})
