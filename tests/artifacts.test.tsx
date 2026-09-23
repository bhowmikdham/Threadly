import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import React from "react"
import { describe, expect, it, vi } from "vitest"

import { ArtifactCard } from "../components/ArtifactCard"

const draft: any = {
  artifact_id: "a1",
  task_id: "t1",
  revision: 1,
  is_latest: true,
  draft_envelope: { to: ["alex@example.test"], cc: [], bcc: [], reply: null },
  review: { payload_hash: "hash-1", blockers: [], state: "unreviewed" },
  artifact: {
    kind: "draft",
    content: {
      mode: "new",
      subject: "Update",
      body: "I will review it tomorrow.",
      unresolved_fields: []
    },
    evidence: [],
    assumptions: []
  }
}
const action: any = {
  action_id: "action-1",
  state: "proposed",
  version: 1,
  payload_hash: "exact-hash",
  preview: {
    from_address: "me@example.test",
    to: ["alex@example.test"],
    cc: [],
    bcc: [],
    subject: "Update",
    body: "I will review it tomorrow."
  },
  blockers: [],
  allowed_operations: ["reject"],
  approval_available: true,
  sending_available: true
}
function mockApi(fn: (m: any) => any) {
  vi.mocked(chrome.runtime.sendMessage).mockImplementation(
    async (m: any) =>
      ({ ok: true, data: m.type === "API" ? await fn(m) : null }) as any
  )
}
describe("reviewed draft integration", () => {
  it("never sends on generation or preview; requires checkbox and exact hash", async () => {
    const calls: any[] = []
    mockApi((m) => {
      calls.push(m)
      if (m.path.endsWith("/review")) return draft
      if (m.path.endsWith("/actions")) return action
      return {
        action: { ...action, state: "approved", approval_available: false }
      }
    })
    render(<ArtifactCard value={draft} replace={vi.fn()} report={vi.fn()} />)
    expect(calls).toHaveLength(0)
    fireEvent.click(screen.getByText("Review outgoing email"))
    await screen.findByText("Approve and send")
    expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(false)
    expect(
      (screen.getByText("Approve and send") as HTMLButtonElement).disabled
    ).toBe(true)
    fireEvent.click(screen.getByRole("checkbox"))
    fireEvent.click(screen.getByText("Approve and send"))
    await waitFor(() =>
      expect(
        calls.find((c) => c.path.endsWith("/approve"))?.body
      ).toMatchObject({ expected_version: 1, payload_hash: "exact-hash" })
    )
  })
  it("does not offer approval when server writes are disabled", async () => {
    mockApi((m) =>
      m.path.endsWith("/review")
        ? draft
        : {
            ...action,
            approval_available: false,
            sending_available: false,
            blockers: ["email_writes_disabled"]
          }
    )
    render(<ArtifactCard value={draft} replace={vi.fn()} report={vi.fn()} />)
    fireEvent.click(screen.getByText("Review outgoing email"))
    await screen.findByText("email writes disabled")
    expect(screen.queryByText("Approve and send")).toBeNull()
  })
  it("invalidates a displayed preview immediately when the body is edited", async () => {
    mockApi((m) => (m.path.endsWith("/review") ? draft : action))
    render(<ArtifactCard value={draft} replace={vi.fn()} report={vi.fn()} />)
    fireEvent.click(screen.getByText("Review outgoing email"))
    await screen.findByText("Approve and send")
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "Changed content" }
    })
    expect(screen.queryByText("Approve and send")).toBeNull()
    expect(
      (screen.getByText("Review outgoing email") as HTMLButtonElement).disabled
    ).toBe(true)
  })
  it("saves a revision with exact edited recipients and does not send", async () => {
    const calls: any[] = []
    mockApi((m) => {
      calls.push(m)
      return { ...draft, revision: 2, artifact_id: "a2" }
    })
    const replace = vi.fn()
    render(<ArtifactCard value={draft} replace={replace} report={vi.fn()} />)
    fireEvent.change(screen.getByLabelText("To"), {
      target: { value: "new@example.test" }
    })
    fireEvent.click(screen.getByText("Save edits"))
    await waitFor(() => expect(replace).toHaveBeenCalled())
    expect(calls[0].body.recipients.to).toEqual(["new@example.test"])
    expect(calls[0].body.expected_revision).toBe(1)
    expect(calls).toHaveLength(1)
  })
  it("renders grounded answers safely as text", () => {
    const a: any = {
      ...draft,
      artifact: {
        kind: "answer",
        content: { text: "<img src=x onerror=alert(1)>", found: true },
        evidence: []
      }
    }
    const { container } = render(
      <ArtifactCard value={a} replace={vi.fn()} report={vi.fn()} />
    )
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeTruthy()
    expect(container.querySelector("img")).toBeNull()
  })
})
