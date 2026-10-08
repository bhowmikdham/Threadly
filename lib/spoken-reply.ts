import type { Entry } from "./types"

const WORKING = new Set(["queued", "running"])
const MAX_SPOKEN = 420

// Long results are read up to a sentence boundary; the rest stays in the chat.
function brief(text: string) {
  const clean = text.replace(/\s+/g, " ").trim()
  if (clean.length <= MAX_SPOKEN) return clean
  const cut = clean.slice(0, MAX_SPOKEN)
  const end = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("? "))
  return `${end > 80 ? cut.slice(0, end + 1) : cut + "…"} The rest is in the chat.`
}

/**
 * What Threadly says aloud for a chat entry in voice mode, or null while the
 * answer is still on its way. Drafts, cards and approvals are pointed to
 * rather than read out, and nothing is ever sent by voice. Calendar events are
 * only created after an explicit spoken yes (see calendar-voice.ts).
 */
export function spokenReply(entry: Entry | undefined): string | null {
  if (!entry || entry.pending) return null
  if (entry.error) return `Sorry, that didn't work. ${brief(entry.error)}`
  const task = entry.task
  if (task && WORKING.has(task.state)) return null
  if (task?.state === "needs_clarification")
    return brief(task.question?.prompt || "I need one more detail.")
  if (task && ["failed", "unsupported", "cancelled"].includes(task.state))
    return "I couldn't finish that one. The details are in the chat."
  const expectsArtifacts =
    task?.state === "succeeded" &&
    (task.artifact_id ||
      task.workflow?.steps?.some((s: any) => s.artifact_id) ||
      task.compound?.steps?.some((s: any) => s.artifact_id))
  if (expectsArtifacts && !entry.artifacts?.length) return null
  const artifact = entry.artifacts?.[entry.artifacts.length - 1]?.artifact
  if (artifact?.kind === "draft")
    return artifact.content?.mode === "reply"
      ? "I've drafted a reply. It's in the chat for you to check before anything is sent."
      : "I've drafted that email. It's in the chat for you to check before anything is sent."
  if (artifact?.content?.overview) return brief(artifact.content.overview)
  if (artifact?.content?.text) return brief(artifact.content.text)
  if (entry.message) return brief(entry.message)
  if (entry.answers?.length)
    return brief(entry.answers[entry.answers.length - 1])
  if (entry.inbox) {
    const n = entry.inbox.results.length
    return n === 0
      ? "I couldn't find any matching emails."
      : `I found ${n} email${n === 1 ? "" : "s"}. They're in the chat.`
  }
  if (entry.proposal) return "I've put a plan in the chat for you to confirm."
  if (artifact) return "Done. It's in the chat."
  if (entry.notice) return brief(entry.notice)
  return task?.state === "succeeded" ? "Done. It's in the chat." : null
}

/**
 * True when the reply is a list the user chooses from, such as the top five
 * emails from a search. Voice mode then lifts the orb out of the way so the
 * cards show in the chat, exactly as they would for a typed request.
 */
export function hasChoices(entry: Entry | undefined): boolean {
  if (entry?.calendarActionId && !entry.pending && !entry.error) {
    return true
  }
  return (entry?.inbox?.results.length ?? 0) > 1
}

const YES = new Set([
  "yes",
  "yeah",
  "yep",
  "yup",
  "sure",
  "okay",
  "ok",
  "please",
  "do it",
  "go ahead",
  "create it",
  "create event",
  "create the event",
  "please do",
  "yes please",
  "yes create it",
  "yes create event",
  "sure thing",
  "that's right",
  "correct",
  "confirm",
  "approve",
  "book it"
])
const NO = new Set([
  "no",
  "nope",
  "nah",
  "cancel",
  "don't",
  "do not",
  "stop",
  "never mind",
  "nevermind",
  "don't do it",
  "no thanks",
  "no thank you",
  "not now",
  "reject"
])

/**
 * Strict yes/no for approvals. Anything else, such as "yes but make it 4pm",
 * returns null so it is handled as a new request and nothing is created.
 */
export function parseConfirmation(said: string): "yes" | "no" | null {
  const t = said
    .toLowerCase()
    .replace(/[.,!?]/g, "")
    .replace(/\s+/g, " ")
    .trim()
  if (YES.has(t)) return "yes"
  if (NO.has(t)) return "no"
  return null
}