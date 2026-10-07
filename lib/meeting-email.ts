export interface MeetingEmailSource {
  kind: "gmail_message"
  thread_id: string
  message_id: string
}

export interface MeetingEmailDraft {
  source: MeetingEmailSource
  context_snapshot_id: string
  source_subject: string
  source_sender: string
  source_excerpt: string
  source_truncated: boolean
  title: string
  timezone: string
  default_duration_minutes: number
  preferences_version: number
  calendars: { id: string; name: string }[]
  confirmation_required: true
}
