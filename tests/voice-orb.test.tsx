import { act, fireEvent, render, screen } from "@testing-library/react"
import React from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { VoiceOrb } from "../components/VoiceOrb"
import { spokenReply } from "../lib/spoken-reply"

// jsdom has no WebGL; the orb simply skips drawing.
HTMLCanvasElement.prototype.getContext = (() => null) as any
let recognizer: any
class FakeRecognition {
  onresult: any
  onerror: any
  onend: any
  start = vi.fn()
  stop = vi.fn()
  abort = vi.fn()
  constructor() {
    recognizer = this
  }
}
const spoken: string[] = []
beforeEach(() => {
  vi.useFakeTimers()
  spoken.length = 0
  ;(window as any).webkitSpeechRecognition = FakeRecognition
  ;(globalThis as any).SpeechSynthesisUtterance = class {
    onstart: any
    onend: any
    constructor(public text: string) {}
  }
  ;(window as any).speechSynthesis = {
    getVoices: () => [],
    cancel: vi.fn(),
    speak: (u: any) => {
      spoken.push(u.text)
      setTimeout(() => u.onend(), 10)
    }
  }
})
afterEach(() => {
  vi.useRealTimers()
  delete (window as any).webkitSpeechRecognition
})
const say = (text: string) =>
  act(() =>
    recognizer.onresult({
      results: [Object.assign([{ transcript: text }], { isFinal: true })]
    })
  )

describe("voice conversation", () => {
  it("sends what was said after a pause, answers aloud, then listens again", async () => {
    const respond = vi.fn().mockResolvedValue("You have two new emails.")
    render(<VoiceOrb respond={respond} onClose={vi.fn()} />)
    expect(screen.getByRole("status").textContent).toBe("Listening…")
    say("What's new in my inbox")
    // No words are shown; only the orb and its state.
    expect(screen.queryByText("What's new in my inbox")).toBeNull()
    await act(() => vi.advanceTimersByTimeAsync(1300))
    expect(respond).toHaveBeenCalledWith("What's new in my inbox")
    expect(spoken).toEqual(["You have two new emails."])
    await act(() => vi.advanceTimersByTimeAsync(20))
    expect(screen.getByRole("status").textContent).toBe("Listening…")
    expect(recognizer.start).toHaveBeenCalledTimes(2)
  })
  it("docks the orb once it has spoken, so the chat stays readable", async () => {
    const { container } = render(
      <VoiceOrb
        respond={vi.fn().mockResolvedValue("You have two new emails.")}
        onClose={vi.fn()}
      />
    )
    expect(container.querySelector(".docked")).toBeNull()
    say("What's new in my inbox")
    await act(() => vi.advanceTimersByTimeAsync(1300))
    await act(() => vi.advanceTimersByTimeAsync(20))
    // Back to listening, but still docked.
    expect(screen.getByRole("status").textContent).toBe("Listening…")
    expect(container.querySelector(".voice-overlay.docked")).not.toBeNull()
  })
  it("ignores what it hears while it is speaking", async () => {
    let answer: (v: string) => void = () => {}
    const respond = vi.fn(() => new Promise<string>((r) => (answer = r)))
    render(<VoiceOrb respond={respond} onClose={vi.fn()} />)
    say("Summarise this")
    await act(() => vi.advanceTimersByTimeAsync(1300))
    expect(screen.getByRole("status").textContent).toBe("Thinking…")
    say("echo of itself")
    await act(() => vi.advanceTimersByTimeAsync(1300))
    expect(respond).toHaveBeenCalledTimes(1)
    await act(async () => answer("Here it is."))
    expect(spoken).toEqual(["Here it is."])
  })
  it("the close button ends the conversation and stops speaking", () => {
    const onClose = vi.fn()
    render(<VoiceOrb respond={vi.fn()} onClose={onClose} />)
    fireEvent.click(
      screen.getByRole("button", { name: "Close voice conversation" })
    )
    expect(recognizer.abort).toHaveBeenCalled()
    expect((window as any).speechSynthesis.cancel).toHaveBeenCalled()
    expect(onClose).toHaveBeenCalledWith(undefined)
  })
  it("explains when voice is unavailable", () => {
    delete (window as any).webkitSpeechRecognition
    const onClose = vi.fn()
    render(<VoiceOrb respond={vi.fn()} onClose={onClose} />)
    expect(onClose.mock.calls[0][0]).toMatch(/isn’t available/)
  })
})

describe("spoken replies", () => {
  it("waits while the answer is on its way", () => {
    expect(spokenReply({ id: "1", instruction: "x", pending: true })).toBeNull()
    expect(
      spokenReply({
        id: "1",
        instruction: "x",
        task: { task_id: "t", state: "running" } as any
      })
    ).toBeNull()
    expect(
      spokenReply({
        id: "1",
        instruction: "x",
        task: { task_id: "t", state: "succeeded", artifact_id: "a" } as any
      })
    ).toBeNull()
  })
  it("reads chat replies and summaries, and points to drafts", () => {
    expect(
      spokenReply({ id: "1", instruction: "hey", message: "Hi there!" })
    ).toBe("Hi there!")
    const withArtifact = (artifact: any) =>
      spokenReply({
        id: "1",
        instruction: "x",
        artifacts: [{ artifact } as any]
      })
    expect(
      withArtifact({
        kind: "summary",
        content: { overview: "Bhowmik wants a review by Thursday." }
      })
    ).toBe("Bhowmik wants a review by Thursday.")
    expect(
      withArtifact({ kind: "draft", content: { mode: "reply", body: "..." } })
    ).toMatch(/drafted a reply.*before anything is sent/)
  })
  it("keeps long answers short and points to the chat", () => {
    const long = "This is a sentence about the thread. ".repeat(30)
    const reply = spokenReply({ id: "1", instruction: "x", message: long })!
    expect(reply.length).toBeLessThan(460)
    expect(reply).toMatch(/The rest is in the chat\.$/)
  })
})
