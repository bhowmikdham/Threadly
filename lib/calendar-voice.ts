import { api, errorText, requestId } from "./api"
import type { CalendarAction } from "./types"

/** Fired with the latest action so an on-screen card never shows stale buttons. */
export const CALENDAR_ACTION_EVENT = "threadly:calendar-action"
/** Fired by the card when the user clicks Create event or Cancel, so voice mode can take the screen back. */
export const CALENDAR_DECISION_EVENT = "threadly:calendar-decision"
export const CONFIRM_WINDOW_MS = 2 * 60_000
const MANUAL =
  "I've prepared the event. Manual approval is required, so please review it in the chat."

export type VoiceConfirm = {
  onYes: () => Promise<VoiceCalendarReply>
  onNo: () => Promise<VoiceCalendarReply>
  expiresAt: number
}
export type VoiceCalendarReply = {
  text: string
  showChat?: boolean
  confirm?: VoiceConfirm
}

const path = (id: string) => `/assistant/calendar-actions/${id}`
const publish = (action: CalendarAction) =>
  window.dispatchEvent(
    new CustomEvent(CALENDAR_ACTION_EVENT, { detail: action })
  )
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/** The event as it is read aloud, so the user approves exactly what they hear. */
export function describeEvent(action: CalendarAction) {
  const { event } = action.preview
  const zone = event.start.timeZone
  const day = new Intl.DateTimeFormat(undefined, {
    timeZone: zone,
    weekday: "long",
    day: "numeric",
    month: "long"
  }).format(new Date(event.start.dateTime))
  const time = (v: string) =>
    new Intl.DateTimeFormat(undefined, {
      timeZone: zone,
      hour: "numeric",
      minute: "2-digit"
    }).format(new Date(v))
  const guests = event.attendees.length
  return (
    `${event.summary}, on ${day}, from ${time(event.start.dateTime)} to ${time(event.end.dateTime)}` +
    (event.location ? `, at ${event.location}` : "") +
    (guests
      ? `. This will send invitations to ${guests} ${guests === 1 ? "person" : "people"}`
      : "")
  )
}

const FINAL = [
  "succeeded",
  "failed",
  "outcome_unknown",
  "rejected",
  "cancelled",
  "expired",
  "superseded"
]

async function outcome(id: string, first: CalendarAction) {
  let action = first
  for (let i = 0; i < 20 && !FINAL.includes(action.state); i++) {
    await sleep(1500)
    action = await api<CalendarAction>(path(id))
    publish(action)
  }
  if (action.state === "succeeded")
    return "Done. I've added it to your calendar."
  if (action.state === "outcome_unknown")
    return "Google hasn't confirmed it yet. Check the chat before trying again, so we don't create a duplicate."
  if (action.state === "executing" || action.state === "approved")
    return "It's still being created. You can see the status in the chat."
  return "I couldn't create that event. The details are in the chat."
}

/**
 * Voice reply for a calendar proposal: lifts the card into view, reads the
 * event aloud and asks for approval. A spoken "yes" approves exactly the
 * version that was read out, using the same call as the card's button.
 */
export async function calendarVoiceReply(
  id: string,
  seed?: CalendarAction
): Promise<VoiceCalendarReply> {
  let action: CalendarAction
  try {
    action = await api<CalendarAction>(path(id))
  } catch {
    if (!seed) return { text: MANUAL, showChat: true }
    action = seed
  }
  publish(action)
  const heard = { version: action.version, hash: action.payload_hash }
  if (action.state === "succeeded")
    return { text: "That event is already on your calendar.", showChat: true }
  if (
    action.state !== "proposed" ||
    !action.approval_available ||
    action.blockers.length
  )
    return { text: MANUAL, showChat: true }
  const key = requestId()
  const confirm: VoiceConfirm = {
    expiresAt: Date.now() + CONFIRM_WINDOW_MS,
    onYes: async () => {
      try {
        const latest = await api<CalendarAction>(path(id))
        publish(latest)
        if (
          latest.state !== "proposed" ||
          !latest.approval_available ||
          latest.version !== heard.version ||
          latest.payload_hash !== heard.hash
        )
          return {
            text: "That event changed since I read it out, so I haven't created it. Check the chat.",
            showChat: true
          }
        const result = await api<any>(`${path(id)}/approve`, {
          request_id: key,
          expected_version: latest.version,
          payload_hash: latest.payload_hash
        })
        const next: CalendarAction = result.action || result
        publish(next)
        const text = await outcome(id, next)
        // Once it's created the orb returns to full screen; any problem keeps
        // the card in view.
        return { text, showChat: !text.startsWith("Done") }
      } catch (e) {
        return { text: `I couldn't create it. ${errorText(e)}`, showChat: true }
      }
    },
    onNo: async () => {
      try {
        const result = await api<any>(`${path(id)}/reject`, {
          request_id: requestId(),
          expected_version: heard.version
        })
        publish(result.action || result)
      } catch {}
      return { text: "Okay, I won't create it.", showChat: false }
    }
  }
  return {
    text: `I've prepared this event: ${describeEvent(action)}. It's in the chat for you to review. Shall I create it?`,
    showChat: true,
    confirm
  }
}