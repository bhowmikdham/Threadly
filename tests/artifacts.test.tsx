import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { ArtifactCard } from "../components/ArtifactCard"
import { GmailDraftCard } from "../components/GmailDraftCard"

const user = { id: 1, email: "me@example.test", name: "Tester" }
const draft: any = {
  artifact_id: "a1",
  task_id: "t1",
  revision: 1,
  is_latest: true,
  draft_envelope: {
    from_address: user.email,
    to: ["alex@example.test"],
    cc: [],
    bcc: [],
    reply: null
  },
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
const caps = {
  account: { account_version: 4 },
  capabilities: [
    { id: "gmail_draft", ready: true },
    { id: "gmail_send", ready: false }
  ]
}
function mockApi(fn: (m: any) => any = () => null, capabilities: any = caps) {
  const calls: any[] = []
  vi.mocked(chrome.runtime.sendMessage).mockImplementation(async (m: any) => {
    calls.push(m)
    return {
      ok: true,
      data: m.path === "/assistant/capabilities" ? capabilities : await fn(m)
    } as any
  })
  return calls
}
const saved = (m: any, state = "succeeded") => ({
  save_id: "save-1",
  source_artifact_id: m.body.artifact_id,
  state,
  preview: { ...m.body, ...m.body.recipients }
})
function card(extra = {}) {
  return (
    <GmailDraftCard
      artifact={draft}
      user={user}
      conversation={{ id: "chat", version: 3 }}
      {...extra}
    />
  )
}
async function ready() {
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Create draft"
        }) as HTMLButtonElement
      ).disabled
    ).toBe(false)
  )
}

describe("editable Gmail draft card", () => {
  it("saves exactly the edited subject, body and all recipients only after a click", async () => {
    const calls = mockApi((m) => (m.method === "POST" ? saved(m) : null))
    render(card())
    await ready()
    fireEvent.change(screen.getByLabelText("Subject"), {
      target: { value: "Edited subject" }
    })
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "  Exact edits 👋\nNo terminal newline  " }
    })
    fireEvent.change(screen.getByLabelText("To"), {
      target: { value: "new@example.test" }
    })
    fireEvent.click(screen.getByText("Cc / Bcc"))
    fireEvent.change(screen.getByLabelText("Cc"), {
      target: { value: "cc@example.test" }
    })
    fireEvent.change(screen.getByLabelText("Bcc"), {
      target: { value: "secret@example.test" }
    })
    expect(calls.filter((m) => m.method === "POST")).toHaveLength(0)
    fireEvent.click(screen.getByRole("button", { name: "Create draft" }))
    await screen.findByText("Saved to Gmail Drafts")
    const post = calls.find((m) => m.method === "POST")
    expect(post.body).toMatchObject({
      artifact_id: "a1",
      expected_revision: 1,
      conversation_id: "chat",
      expected_version: 3,
      subject: "Edited subject",
      body: "  Exact edits 👋\nNo terminal newline  ",
      recipients: {
        to: ["new@example.test"],
        cc: ["cc@example.test"],
        bcc: ["secret@example.test"]
      },
      account_version: 4
    })
    expect(post.expectedUserId).toBe(1)
    expect(
      calls.filter((m) => /send|approve|review|actions/.test(m.path))
    ).toHaveLength(0)
    expect(screen.queryByRole("button", { name: /send/i })).toBeNull()
    expect(
      screen
        .getByRole("link", { name: "Open Gmail Drafts" })
        .getAttribute("href")
    ).toContain("#drafts")
  })
  it("does not show success until confirmation and prevents rapid duplicate clicks", async () => {
    let resolve: (v: any) => void
    let pending: any
    const calls = mockApi((m) =>
      m.method === "POST"
        ? new Promise((r) => {
            resolve = r
            pending = m
          })
        : null
    )
    render(card())
    await ready()
    const button = screen.getByRole("button", { name: "Create draft" })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(screen.queryByText("Saved to Gmail Drafts")).toBeNull()
    expect(
      (screen.getByLabelText("Message") as HTMLTextAreaElement).disabled
    ).toBe(true)
    resolve(saved(pending))
    await screen.findByText("Saved to Gmail Drafts")
    expect(calls.filter((m) => m.method === "POST")).toHaveLength(1)
  })
  it("keeps a named recipient editable and requires their address only on Create draft", async () => {
    const calls = mockApi((m) => (m.method === "POST" ? saved(m) : null))
    render(
      <GmailDraftCard
        draft={{
          draft_id: "named",
          recipient: "Alex",
          subject: "Thanks",
          body: "Thank you.",
          unresolved_fields: []
        }}
        user={user}
        conversation={{ id: "chat", version: 1 }}
      />
    )
    await ready()
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "Thanks for your help." }
    })
    expect(screen.queryByRole("alert")).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Create draft" }))
    await screen.findByRole("alert")
    expect(calls.filter((m) => m.method === "POST")).toHaveLength(0)
    fireEvent.change(screen.getByLabelText("To"), {
      target: { value: "alex@example.test" }
    })
    fireEvent.click(screen.getByRole("button", { name: "Create draft" }))
    await screen.findByText("Saved to Gmail Drafts")
    expect(calls.find((m) => m.method === "POST").body).toMatchObject({
      draft_id: "named",
      body: "Thanks for your help."
    })
  })
  it("restores a submitted draft without creating a new one", async () => {
    const receipt = {
      state: "succeeded",
      source_artifact_id: "a1",
      preview: {
        to: ["saved@example.test"],
        cc: [],
        bcc: [],
        subject: "Saved subject",
        body: "Saved edits"
      }
    }
    const calls = mockApi(() => receipt)
    render(card())
    await screen.findByText("Saved to Gmail Drafts")
    expect(
      (screen.getByLabelText("Subject") as HTMLTextAreaElement).value
    ).toBe("Saved subject")
    expect(screen.queryByRole("button", { name: "Create draft" })).toBeNull()
    expect(calls.every((m) => m.method === "GET")).toBe(true)
  })
  it("freezes a lost response and replays only identical content and request ID", async () => {
    const writes: any[] = []
    mockApi((m) => {
      if (m.method !== "POST") return null
      writes.push(m.body)
      if (writes.length === 1) throw new Error("Connection lost")
      return saved(m)
    })
    render(card())
    await ready()
    fireEvent.click(screen.getByRole("button", { name: "Create draft" }))
    await screen.findByText("Save not confirmed")
    expect(
      (screen.getByLabelText("Message") as HTMLTextAreaElement).disabled
    ).toBe(true)
    fireEvent.click(screen.getByRole("button", { name: "Retry same save" }))
    await screen.findByText("Saved to Gmail Drafts")
    expect(writes[1]).toEqual(writes[0])
  })
  it("does not retry a provider outcome that is unknown", async () => {
    const calls = mockApi((m) =>
      m.method === "POST" ? saved(m, "outcome_unknown") : null
    )
    render(card())
    await ready()
    fireEvent.click(screen.getByRole("button", { name: "Create draft" }))
    await screen.findByText("Waiting for confirmation")
    expect(
      screen.queryByRole("button", { name: /Create draft|Retry same save/ })
    ).toBeNull()
    expect(calls.filter((m) => m.method === "POST")).toHaveLength(1)
  })
  it("keeps fields when permission is missing without requesting OAuth", async () => {
    const calls = mockApi(() => null, {
      ...caps,
      capabilities: [
        { id: "gmail_draft", ready: false, status: "scope_missing" },
        { id: "gmail_send", ready: true }
      ]
    })
    render(card())
    await screen.findByText(/doesn’t have Gmail draft access/)
    expect(
      (
        screen.getByRole("button", {
          name: "Create draft"
        }) as HTMLButtonElement
      ).disabled
    ).toBe(true)
    expect(
      (screen.getByLabelText("Subject") as HTMLTextAreaElement).disabled
    ).toBe(false)
    expect(calls.every((m) => m.method === "GET")).toBe(true)
  })
  it("blocks stale chat submissions while allowing the user to copy edits", async () => {
    mockApi()
    render(card({ enabled: false }))
    await screen.findByText(/This chat or draft has changed/)
    expect(
      (
        screen.getByRole("button", {
          name: "Create draft"
        }) as HTMLButtonElement
      ).disabled
    ).toBe(true)
    fireEvent.change(screen.getByLabelText("Subject"), {
      target: { value: "Local changes" }
    })
    fireEvent.click(screen.getByText("Copy"))
    await waitFor(() =>
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        expect.stringContaining("Local changes")
      )
    )
  })
  it("unmounting during readiness cannot start a save", async () => {
    let resolve: (v: any) => void
    const calls = mockApi(
      () =>
        new Promise((r) => {
          resolve = r
        })
    )
    const view = render(card())
    view.unmount()
    resolve(null)
    await Promise.resolve()
    expect(calls.every((m) => m.method === "GET")).toBe(true)
  })
  it("never overwrites a newer generated revision with an older saved snapshot", async () => {
    mockApi(() => ({
      state: "succeeded",
      source_artifact_id: "a1",
      preview: {
        to: ["old@example.test"],
        cc: [],
        bcc: [],
        subject: "Old subject",
        body: "Old saved text"
      }
    }))
    render(
      card({
        artifact: {
          ...draft,
          artifact_id: "a2",
          revision: 2,
          artifact: {
            ...draft.artifact,
            content: {
              ...draft.artifact.content,
              body: "New generated revision"
            }
          }
        }
      })
    )
    await screen.findByText("Earlier revision saved")
    expect(
      (screen.getByLabelText("Message") as HTMLTextAreaElement).value
    ).toBe("New generated revision")
    expect(
      (screen.getByLabelText("Message") as HTMLTextAreaElement).disabled
    ).toBe(false)
    expect(screen.queryByRole("button", { name: "Create draft" })).toBeNull()
  })
  it("renders grounded answers as text", () => {
    const a: any = {
      ...draft,
      artifact: {
        kind: "answer",
        content: { text: "<img src=x onerror=alert(1)>" },
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
