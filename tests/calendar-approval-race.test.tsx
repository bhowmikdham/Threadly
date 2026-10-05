import { act, fireEvent, render, screen } from "@testing-library/react"
import { expect, it, vi } from "vitest"

import { CalendarApprovalMenu } from "../components/CalendarApprovalMenu"

it("ignores a permission save response after switching to a different chat", async () => {
  let completeSave!: (value: unknown) => void
  const save = new Promise((resolve) => {
    completeSave = resolve
  })
  vi.mocked(chrome.runtime.sendMessage).mockImplementation(async (m: any) => {
    if (m.method === "PUT") return await save
    return { ok: true, data: { mode: "ask", version: 0 } }
  })
  const props = {
    remember: vi.fn().mockResolvedValue(undefined),
    onSaving: vi.fn()
  }
  const { rerender } = render(
    <CalendarApprovalMenu conversationId="chat-1" {...props} />
  )
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Calendar approval: Ask for approval"
    })
  )
  fireEvent.click(screen.getByRole("menuitemradio", { name: /Always allow/ }))
  await act(async () => {
    await Promise.resolve()
  })
  rerender(<CalendarApprovalMenu conversationId="chat-2" {...props} />)
  await screen.findByRole("button", {
    name: "Calendar approval: Ask for approval"
  })
  await act(async () => {
    completeSave({ ok: true, data: { mode: "always", version: 1 } })
    await save
  })
  expect(
    screen.getByRole("button", { name: "Calendar approval: Ask for approval" })
  ).toBeTruthy()
  expect(
    screen.queryByRole("button", { name: "Calendar approval: Always allow" })
  ).toBeNull()
})
