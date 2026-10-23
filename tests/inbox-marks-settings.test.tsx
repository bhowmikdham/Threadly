import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import React from "react"
import { describe, expect, it, vi } from "vitest"

import { InboxMarksSettings } from "../components/InboxMarksSettings"
import { INBOX_MARKS_KEY } from "../lib/inbox-marks"

describe("inbox display settings", () => {
  it("retains the saved preference when a write fails and lets the user retry", async () => {
    vi.mocked(chrome.storage.local.get).mockResolvedValueOnce({
      [INBOX_MARKS_KEY]: { enabled: true, words: false }
    } as never)
    vi.mocked(chrome.storage.local.set)
      .mockRejectedValueOnce(new Error("disk unavailable"))
      .mockResolvedValueOnce(undefined)
    render(<InboxMarksSettings />)
    const words = screen.getByRole("checkbox", {
      name: "Rather have words than icons"
    }) as HTMLInputElement
    await waitFor(() => expect(words.disabled).toBe(false))
    fireEvent.click(words)
    await screen.findByRole("alert")
    expect(words.checked).toBe(false)
    fireEvent.click(words)
    await waitFor(() => expect(words.checked).toBe(true))
    expect(screen.queryByRole("alert")).toBeNull()
  })
  it("keeps controls disabled if protected storage cannot be read", async () => {
    vi.mocked(chrome.storage.local.get).mockRejectedValueOnce(
      new Error("unavailable")
    )
    render(<InboxMarksSettings />)
    await screen.findByRole("alert")
    for (const control of screen.getAllByRole("checkbox"))
      expect((control as HTMLInputElement).disabled).toBe(true)
  })
})
