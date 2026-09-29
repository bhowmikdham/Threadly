import { act, fireEvent, render, screen } from "@testing-library/react"
import React from "react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { VoiceOrb } from "../components/VoiceOrb"

// jsdom has no WebGL; the orb simply skips drawing.
HTMLCanvasElement.prototype.getContext = (() => null) as any
let recognizer: any
class FakeRecognition {
  onresult: any
  onerror: any
  onend: any
  start = vi.fn()
  stop = vi.fn()
  constructor() {
    recognizer = this
  }
}
afterEach(() => {
  delete (window as any).webkitSpeechRecognition
})
const say = (final: string, interim = "") =>
  act(() =>
    recognizer.onresult({
      results: [
        Object.assign([{ transcript: final }], { isFinal: true }),
        ...(interim
          ? [Object.assign([{ transcript: interim }], { isFinal: false })]
          : [])
      ]
    })
  )

describe("voice orb", () => {
  it("listens, shows the words live and hands them over on close", () => {
    ;(window as any).webkitSpeechRecognition = FakeRecognition
    const onClose = vi.fn()
    render(<VoiceOrb onClose={onClose} />)
    expect(screen.getByRole("dialog", { name: "Voice input" })).toBeTruthy()
    expect(recognizer.start).toHaveBeenCalled()
    say("Summarise this thread", " please")
    expect(screen.getByText("Summarise this thread")).toBeTruthy()
    expect(screen.getByText("please")).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Close voice input" }))
    expect(recognizer.stop).toHaveBeenCalled()
    expect(onClose).toHaveBeenCalledWith(
      "Summarise this thread please",
      undefined
    )
  })
  it("keeps listening through Chrome's pauses until closed", () => {
    ;(window as any).webkitSpeechRecognition = FakeRecognition
    render(<VoiceOrb onClose={vi.fn()} />)
    recognizer.onend()
    expect(recognizer.start).toHaveBeenCalledTimes(2)
  })
  it("Escape closes it too, with nothing said", () => {
    ;(window as any).webkitSpeechRecognition = FakeRecognition
    const onClose = vi.fn()
    render(<VoiceOrb onClose={onClose} />)
    fireEvent.keyDown(window, { key: "Escape" })
    expect(onClose).toHaveBeenCalledWith("", undefined)
  })
  it("explains when dictation is unavailable", () => {
    const onClose = vi.fn()
    render(<VoiceOrb onClose={onClose} />)
    expect(onClose.mock.calls[0][1]).toMatch(/isn’t available/)
  })
})
