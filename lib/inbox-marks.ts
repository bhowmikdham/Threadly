export const INBOX_MARKS_KEY = "threadlyInboxMarks"
export type InboxMarks = { enabled: boolean; words: boolean }

// Preserve the badges already shipped in 0.2.4. Only explicit false disables.
export function inboxMarks(value: unknown): InboxMarks {
  const saved = value as Partial<InboxMarks> | null
  return { enabled: saved?.enabled !== false, words: saved?.words === true }
}
