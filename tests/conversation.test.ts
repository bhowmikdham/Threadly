import { describe, expect, it } from "vitest"

import { chatAnswer, isRefinement, refinement } from "../lib/conversation"

const summary: any = {
  id: "e",
  instruction: "Summarise this thread.",
  task: { state: "succeeded", context_snapshot_id: "original-context" },
  artifacts: [{ artifact: { kind: "summary" } }]
}
describe("conversation references", () => {
  it.each([
    "Make it shorter",
    "Please make that more formal.",
    "Shorten the summary",
    "Could you rewrite it more warmly?"
  ])("binds refinement: %s", (text) => expect(isRefinement(text)).toBe(true))
  it.each([
    "Send it",
    "Make it shorter and send it",
    "What is in the third message?",
    "Draft a new email",
    "Summarise another email"
  ])("does not treat a new operation as refinement: %s", (text) =>
    expect(isRefinement(text)).toBe(false)
  )
  it("retains original source and user request without importing the assistant answer as facts", () => {
    const r = refinement(summary, "Make it shorter")
    expect(r.contextId).toBe("original-context")
    expect(r.hint).toBe("summarise")
    expect(r.instruction).toContain("Summarise this thread.")
    expect(r.draft).toBeNull()
  })
  it("preserves edited draft envelope and reply target while regenerating wording", () => {
    const r = refinement(
      {
        ...summary,
        artifacts: [
          {
            artifact: { kind: "draft" },
            draft_envelope: {
              to: ["edited@example.test"],
              cc: [],
              bcc: [],
              reply: { gmail_message_id: "m1" }
            }
          }
        ]
      } as any,
      "Make it shorter"
    )
    expect(r.hint).toBe("reply")
    expect(r.draft).toEqual({
      to: ["edited@example.test"],
      cc: [],
      bcc: [],
      reply_message_id: "m1"
    })
  })
  it("does not silently discard manually saved draft edits", () => {
    expect(() =>
      refinement(
        {
          ...summary,
          artifacts: [{ revision: 2, artifact: { kind: "draft" } }]
        } as any,
        "Make it shorter"
      )
    ).toThrow("saved edits")
  })
  it("does not pick one of several outputs or revive a failed task", () => {
    expect(
      refinement(
        { ...summary, artifacts: [...summary.artifacts, ...summary.artifacts] },
        "Make it shorter"
      )
    ).toBeNull()
    expect(
      refinement(
        { ...summary, task: { state: "failed" } } as any,
        "Make it shorter"
      )
    ).toBeNull()
  })
  it("answers only a single requested typed field; never routes an approval", () => {
    const q = (fields: string[], expired = false) =>
      ({
        task: { state: "needs_clarification", question: { fields, expired } }
      }) as any
    expect(chatAnswer(q(["recipients"]), "alex@example.test")).toEqual({
      recipients: ["alex@example.test"]
    })
    expect(chatAnswer(q(["am_or_pm"]), "pm")).toEqual({ am_or_pm: "PM" })
    expect(chatAnswer(q(["duration_minutes"]), "30 minutes")).toEqual({
      duration_minutes: 30
    })
    expect(chatAnswer(q(["recipients"]), "send it")).toBeNull()
    expect(
      chatAnswer(q(["recipients", "reply_message_id"]), "alex@example.test")
    ).toBeNull()
    expect(chatAnswer(q(["recipients"], true), "alex@example.test")).toBeNull()
  })
})
