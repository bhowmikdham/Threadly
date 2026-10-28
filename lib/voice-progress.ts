// Presentation only: every utterance still goes to the same conversation API.
// These exclusions never infer a task, cancel work or grant permission.
export const PROGRESS_DELAY_MS = 2500
export function progressCue(said: string, previousCues: number): string | null {
  const text = said
    .toLowerCase()
    .replace(/[’']/g, "")
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim()
  const greeting =
    /^(?:(?:hey|hi|hello|good morning|good afternoon|good evening)(?: there)?(?: threadly)?(?: how(?: are you| are you doing| is it going|s it going))?|(?:threadly )?how(?: are you| are you doing| is it going|s it going)|whats up)(?: today)?$/
  const social =
    /^(?:(?:im |i am )?(?:good|fine|well|not bad)(?: thanks| thank you)?(?: and you| how about you)?|(?:thanks|thank you)(?: threadly| so much| thats all)?|(?:yes|yep|yeah|no|nope|ok|okay|sure|not now)|(?:cancel\s*)+|stop|never mind|nevermind|no thank you|no thanks|thats all|bye|goodbye)$/
  if (
    !text ||
    greeting.test(text) ||
    social.test(text) ||
    /^(?:cancel|stop|never mind|nevermind|actually|no|instead)\b/.test(text)
  )
    return null
  return previousCues % 2 === 0
    ? "I'm still working on your request."
    : "This is taking a little longer."
}
