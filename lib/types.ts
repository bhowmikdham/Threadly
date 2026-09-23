export interface User {
  id: number
  email: string
  name: string | null
}
export interface Capability {
  id: string
  ready: boolean
  enabled: boolean
  status: string
}
export interface MailMessage {
  gmail_msg_id: string
  subject: string
  from_addr: string
  sent_at?: string
  received_at?: string
  body_clean: string
  is_from_user?: boolean
}
export interface Selection {
  thread: { thread_id: string; subject: string; version: number }
  messages: MailMessage[]
  selectedIds: string[]
  targetId: string | null
}
export interface Task {
  task_id: string
  effective_context_snapshot_id?: string
  instruction: string
  state: string
  version: number
  artifact_id: string | null
  error_code: string | null
  question?: any
  route?: any
  workflow?: any
  compound?: any
  scheduling?: any
  context_snapshot_id?: string
  effective_draft_input?: any
  draft_input?: any
}
export interface Artifact {
  stream_key?: string
  is_final_result?: boolean
  artifact_id: string
  task_id: string
  revision: number
  artifact: {
    kind: string
    content: any
    evidence: any[]
    coverage?: string
    assumptions?: string[]
  }
  draft_envelope?: any
  review?: any
  is_latest?: boolean
  latest_artifact_id?: string
  scheduling_status?: any
}
export interface Entry {
  id: string
  instruction: string
  task?: Task
  recipe?: {
    instruction: string
    contextId: string | null
    hint?: string
    draft?: any
  }
  answers?: string[]
  proposal?: any
  artifacts?: Artifact[]
  error?: string
  notice?: string
  pending?: boolean
}
export interface EmailAction {
  action_id: string
  state: string
  version: number
  payload_hash: string
  preview: any
  blockers: string[]
  approval_available: boolean
  sending_available: boolean
  allowed_operations: string[]
  recovery?: { guidance: string }
  error_code?: string
}
