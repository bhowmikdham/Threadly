import { addresses } from "./api"
import type { Entry } from "./types"

export const CONVERSATION_RELEASE = "conversation-adapter-1.0.0"

// These recognize an edit of the immediately preceding result, not a workflow intent.
// All new commands still go through the backend router. Never infer send authority.
export function isRefinement(text: string) {
  return (
    /^(?:(?:please|can you|could you)\s+)?(?:(?:make|keep)\s+(?:it|that|this|the (?:reply|draft|summary))\s+(?:shorter|longer|brief|concise|more concise|more formal|less formal|friendlier|warmer|simpler|one sentence|two sentences|a single sentence|bullet points)|(?:shorten|expand|simplify|rephrase|rewrite)\s+(?:it|that|this|the (?:reply|draft|summary)))(?:[.!?]|\s|$)/i.test(
      text.trim()
    ) &&
    !/\b(send|book|delete|forward|recipients?|instead|another|different)\b/i.test(
      text
    )
  )
}
export function refinement(entry: Entry | undefined, text: string) {
  if (!entry || !isRefinement(text)) return null
  const results =
    entry.artifacts?.filter((a) =>
      ["summary", "draft"].includes(a.artifact.kind)
    ) || []
  if (results.length !== 1 || entry.task?.state !== "succeeded") return null
  const a = results[0]
  if (a.artifact.kind === "draft" && a.revision > 1)
    throw new Error(
      "This draft has saved edits. Use Edit draft to preserve those exact changes; conversational regeneration currently starts from the original request."
    )
  const original =
    entry.recipe?.instruction || entry.task.instruction || entry.instruction
  const instruction = `${original}\n\nUser refinement of this same request: ${text}`
  if (instruction.length > 4000)
    throw new Error(
      "Start a new request with the changes you want; this conversation has reached its refinement limit."
    )
  return {
    instruction,
    contextId:
      entry.recipe?.contextId ||
      entry.task.effective_context_snapshot_id ||
      entry.task.context_snapshot_id ||
      null,
    hint:
      a.artifact.kind === "summary"
        ? "summarise"
        : a.draft_envelope?.reply
          ? "reply"
          : "compose",
    draft:
      a.artifact.kind === "draft"
        ? {
            to: a.draft_envelope.to,
            cc: a.draft_envelope.cc,
            bcc: a.draft_envelope.bcc,
            reply_message_id: a.draft_envelope.reply?.gmail_message_id || null
          }
        : null
  }
}
export function chatAnswer(entry: Entry | undefined, text: string) {
  const q = entry?.task?.question
  if (entry?.task?.state !== "needs_clarification" || !q || q.expired)
    return null
  // Multi-field questions retain contextual controls. Do not guess a timezone or message ID.
  if (q.fields?.length !== 1) return null
  const field = q.fields[0]
  if (field === "recipients") {
    try {
      const values = addresses(text.trim())
      return values.length ? { [field]: values } : null
    } catch {
      return null
    }
  }
  if (
    ["am_or_pm", "meridiem"].includes(field) &&
    /^(am|pm)$/i.test(text.trim())
  )
    return { [field]: text.trim().toUpperCase() }
  if (
    field === "duration_minutes" &&
    /^\d{1,3}(?:\s*(?:minutes?|mins?))?$/i.test(text.trim())
  )
    return { [field]: parseInt(text, 10) }
  return null
}
