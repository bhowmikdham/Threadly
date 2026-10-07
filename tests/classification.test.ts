import { describe, expect, it } from "vitest"

import {
  categoryDisplay,
  categoryIcon,
  priorityTip,
  validClassification
} from "../lib/classification"

// Shapes from release/backend docs/classification/frontend-fixtures.json.
const base = {
  schema_version: "email-classification-with-action.v1",
  thread_id: "abc123",
  source_message_ids: ["def456"],
  valid_until: "2026-10-06T02:05:00Z",
  persisted: false
}
const classified = {
  ...base,
  status: "classified",
  labels: {
    needs_reply: true,
    priority: "Medium",
    category: "legal_contracts",
    action: "review"
  }
}

describe("classification responses", () => {
  it("accepts classified, needs-review and skipped results for the same thread", () => {
    expect(validClassification(classified, "abc123")).toBe(true)
    for (const status of ["needs_review", "skipped"])
      expect(
        validClassification({ ...base, status, labels: null }, "abc123")
      ).toBe(true)
  })
  it("rejects another thread's result, unknown labels and labels without a status", () => {
    expect(validClassification(classified, "other1")).toBe(false)
    expect(
      validClassification(
        {
          ...classified,
          labels: { ...classified.labels, category: "toString" }
        },
        "abc123"
      )
    ).toBe(false)
    expect(
      validClassification(
        { ...classified, labels: { ...classified.labels, priority: "Urgent" } },
        "abc123"
      )
    ).toBe(false)
    expect(
      validClassification(
        { ...base, status: "needs_review", labels: classified.labels },
        "abc123"
      )
    ).toBe(false)
  })
  it("has an icon for every server category", () => {
    for (const category of Object.keys(categoryDisplay))
      expect(categoryIcon[category]).toContain("<svg")
  })
  it("says the priority, whether a reply is needed, and the action", () => {
    expect(priorityTip(classified.labels as any)).toBe(
      "Medium priority · reply needed · Review"
    )
    expect(
      priorityTip({
        needs_reply: false,
        priority: "Low",
        category: "other",
        action: "no_action"
      })
    ).toBe("Low priority · no reply needed")
  })
})
