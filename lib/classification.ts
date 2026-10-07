/**
 * Inbox badges from POST /threads/{id}/classification. Contract:
 * release/backend docs/classification (README, types.ts, response.schema.json).
 */
export type Category =
  | "finance_payments"
  | "hr"
  | "it"
  | "legal_contracts"
  | "meeting_scheduling"
  | "other"
  | "projects"
export type Priority = "High" | "Medium" | "Low"
export type Action =
  | "approve"
  | "attend"
  | "complete_submit"
  | "edit"
  | "no_action"
  | "reply"
  | "review"
export type Labels = {
  needs_reply: boolean
  priority: Priority
  category: Category
  action: Action
}
export type Classification = {
  schema_version: "email-classification-with-action.v1"
  thread_id: string
  status: "classified" | "needs_review" | "skipped"
  labels: Labels | null
  valid_until: string
}

export const categoryDisplay: Record<Category, string> = {
  finance_payments: "Finance",
  hr: "HR",
  it: "IT",
  legal_contracts: "Legal",
  meeting_scheduling: "Meeting",
  other: "Other",
  projects: "Projects"
}
export const actionDisplay: Record<Action, string> = {
  approve: "Approve",
  attend: "Attend",
  complete_submit: "Complete/Submit",
  edit: "Edit",
  no_action: "No Action",
  reply: "Reply",
  review: "Review"
}

// 16px line icons from the Claude Design handoff (icons/*.svg). IT and
// Projects aren't in the handoff and are drawn to match it.
const line = (paths: string, width = 1.5) =>
  `<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`
const dot = (cx: number, cy: number, r: number) =>
  `<circle cx="${cx}" cy="${cy}" r="${r}" fill="currentColor" stroke="none"/>`
export const priorityIcon: Record<Priority, string> = {
  High: line(
    '<path d="M3.4 7.9 8 3.6l4.6 4.3"/><path d="M3.4 12.4 8 8.1l4.6 4.3"/>',
    1.85
  ),
  Medium: line('<path d="M3.4 10.2 8 5.9l4.6 4.3"/>', 1.85),
  Low: line('<path d="M3.4 5.9 8 10.2l4.6-4.3"/>', 1.85)
}
export const replyIcon = line(
  '<path d="M6.2 2.8 2.6 6.4l3.6 3.6"/><path d="M2.6 6.4h6.6a3.6 3.6 0 0 1 3.6 3.6v3.2"/>'
)
export const categoryIcon: Record<Category, string> = {
  legal_contracts: line(
    `<path d="M8 3.2v10.4"/><path d="M5 13.6h6"/><path d="M3 5.6h10"/><path d="M3 5.6 1.6 9h2.8z"/><path d="M13 5.6 11.6 9h2.8z"/>${dot(8, 2.6, 0.9)}`
  ),
  meeting_scheduling: line(
    `<rect x="2" y="3.4" width="12" height="10.6" rx="1.8"/><path d="M2 6.6h12"/><path d="M5.4 2v2.4"/><path d="M10.6 2v2.4"/>${dot(8, 10.4, 1.4)}`
  ),
  hr: line(
    '<circle cx="6.4" cy="5.4" r="2.6"/><path d="M1.8 13.8a4.6 4.6 0 0 1 9.2 0"/><path d="M11 3.4a2.4 2.4 0 0 1 0 4.4"/><path d="M12.4 10a4 4 0 0 1 1.8 3.2"/>'
  ),
  finance_payments: line(
    '<rect x="1.4" y="4" width="13.2" height="8" rx="1.6"/><circle cx="8" cy="8" r="1.9"/><path d="M4.2 8h-0.01"/><path d="M11.8 8h-0.01"/>'
  ),
  it: line(
    '<rect x="1.8" y="2.6" width="12.4" height="8.4" rx="1.6"/><path d="M8 11v2.4"/><path d="M5.2 13.6h5.6"/>'
  ),
  projects: line(
    '<path d="M1.8 4.4c0-.7.5-1.2 1.2-1.2h3.1l1.5 1.8H13c.7 0 1.2.5 1.2 1.2v6.4c0 .7-.5 1.2-1.2 1.2H3c-.7 0-1.2-.5-1.2-1.2z"/>'
  ),
  other: line(
    `<path d="M8.6 2h4.6c.5 0 .8.4.8.8v4.6L7.4 13.8a1 1 0 0 1-1.4 0L2.2 10a1 1 0 0 1 0-1.4z"/>${dot(11, 5, 1)}`
  )
}

/** Left-slot tooltip, e.g. "High priority · reply needed · Review". */
export function priorityTip(labels: Labels) {
  return [
    `${labels.priority} priority`,
    labels.needs_reply ? "reply needed" : "no reply needed",
    labels.action !== "no_action" ? actionDisplay[labels.action] : null
  ]
    .filter(Boolean)
    .join(" · ")
}

/** A usable response for this thread; anything else shows no badges. */
export function validClassification(
  value: any,
  threadId: string
): value is Classification {
  if (
    value?.schema_version !== "email-classification-with-action.v1" ||
    value.thread_id !== threadId ||
    !Number.isFinite(Date.parse(value.valid_until))
  )
    return false
  if (value.status !== "classified")
    return ["needs_review", "skipped"].includes(value.status) && !value.labels
  const l = value.labels
  return (
    typeof l?.needs_reply === "boolean" &&
    ["High", "Medium", "Low"].includes(l.priority) &&
    Object.hasOwn(categoryDisplay, l.category) &&
    Object.hasOwn(actionDisplay, l.action)
  )
}
