import { useCallback, useEffect, useRef, useState } from "react"

import { addresses, api, errorText, requestId } from "./api"
import { activeGmail, capture } from "./context"
import type { Artifact, Entry, Mode, Selection, Task, User } from "./types"

const terminal = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "needs_clarification",
  "unsupported"
])
export function useAssistant(user: User) {
  const [entries, setEntries] = useState<Entry[]>([]),
    [selection, setSelection] = useState<Selection | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [preferences, setPreferences] = useState<any>(null)
  const mounted = useRef(true),
    polling = useRef(new Set<string>()),
    submitting = useRef(false)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  const update = useCallback((id: string, value: Partial<Entry>) => {
    if (mounted.current)
      setEntries((old) =>
        old.map((x) => (x.id === id ? { ...x, ...value } : x))
      )
  }, [])
  const artifacts = async (task: Task) => {
    const ids = [
      task.artifact_id,
      ...(task.workflow?.steps || task.compound?.steps || []).map(
        (s) => s.artifact_id
      )
    ].filter(Boolean)
    return Promise.all(
      [...new Set(ids)].map((id) => api<Artifact>(`/assistant/artifacts/${id}`))
    )
  }
  const watch = async (id: string, initial: Task) => {
    if (polling.current.has(initial.task_id)) return initial
    polling.current.add(initial.task_id)
    let task = initial,
      failures = 0
    try {
      while (mounted.current) {
        update(id, { task, pending: false })
        if (terminal.has(task.state)) {
          if (
            task.artifact_id ||
            task.workflow?.steps?.some((s) => s.artifact_id) ||
            task.compound?.steps?.some((s) => s.artifact_id)
          )
            update(id, { artifacts: await artifacts(task) })
          return task
        }
        await new Promise((r) => setTimeout(r, 2000))
        if (!mounted.current) return task
        try {
          task = await api(`/assistant/tasks/${task.task_id}`)
          failures = 0
        } catch (e) {
          if (++failures >= 3) throw e
        }
      }
      return task
    } catch (e) {
      update(id, {
        error: `Status refresh paused: ${errorText(e)} Your task may still be running.`,
        pending: false
      })
      return task
    } finally {
      polling.current.delete(initial.task_id)
    }
  }
  const selectActive = async () => {
    setBusy(true)
    setError("")
    try {
      const s = await activeGmail(user)
      if (mounted.current) {
        setSelection(s)
        if (!s)
          setError(
            "Open an email in Gmail, or choose a thread from Search mail."
          )
      }
    } catch (e) {
      if (mounted.current) setError(errorText(e))
    } finally {
      if (mounted.current) setBusy(false)
    }
  }
  const selectThread = async (id: string) => {
    setBusy(true)
    setError("")
    try {
      const data = await api(`/threads/${encodeURIComponent(id)}`)
      if (mounted.current)
        setSelection({
          ...data,
          selectedIds: data.messages.map((m) => m.gmail_msg_id),
          targetId: null
        })
    } catch (e) {
      if (mounted.current) setError(errorText(e))
    } finally {
      if (mounted.current) setBusy(false)
    }
  }
  const proposal = async (
    id: string,
    instruction: string,
    contextId: string | null,
    draft: any
  ) => {
    let p = preferences
    if (!p) {
      try {
        p = await api("/calendar/preferences")
        if (mounted.current) setPreferences(p)
      } catch (e) {
        if (e.code !== "calendar_preferences_missing" && e.status !== 403)
          throw e
      }
    }
    const value = await api("/assistant/workflow-proposals", {
      schema_version: "1.0",
      request_id: requestId(),
      instruction,
      context_snapshot_id: contextId,
      draft_options: draft,
      expected_preferences_version: p?.version || null
    })
    update(id, { proposal: value, pending: false })
  }
  const submit = async (
    instruction: string,
    mode: Mode,
    to: string,
    replyInWorkflow: boolean
  ) => {
    if (submitting.current || !instruction.trim()) return
    submitting.current = true
    setBusy(true)
    setError("")
    const id = requestId()
    setEntries((old) => [...old, { id, instruction, pending: true }])
    try {
      const using = mode === "compose" ? null : selection
      const ctx = using ? await capture(using) : null
      let draft = null
      if (
        mode === "reply" ||
        mode === "compose" ||
        (mode === "workflow" && !!to.trim())
      ) {
        const isReply =
          mode === "reply" || (mode === "workflow" && replyInWorkflow)
        if (isReply && !using?.targetId)
          throw new Error("Select the specific message to reply to.")
        draft = {
          to: addresses(to),
          cc: [],
          bcc: [],
          ...(isReply ? { reply_message_id: using.targetId } : {})
        }
        if (!draft.to.length)
          throw new Error("Enter the intended recipient address.")
      }
      if (mode === "workflow") {
        await proposal(id, instruction, ctx?.context_snapshot_id || null, draft)
        return
      }
      const options =
        mode === "transform"
          ? { operation: "transform_text", message_id: using?.targetId }
          : undefined
      if (mode === "transform" && !using?.targetId)
        throw new Error("Select one message to rewrite.")
      const task = await api<Task>("/assistant/requests", {
        schema_version: "1.0",
        request_id: id,
        instruction,
        intent_hint:
          mode === "ask" ? null : mode === "transform" ? "other" : mode,
        context_snapshot_id: ctx?.context_snapshot_id || null,
        continuation: null,
        ...(draft ? { draft_options: draft } : {}),
        ...(options ? { read_options: options } : {})
      })
      const done = await watch(id, task)
      const route = done?.route?.decision
      if (
        mounted.current &&
        done?.state === "unsupported" &&
        route?.requested_action === "none" &&
        (route?.operations?.length > 1 || route?.intent === "plan_schedule")
      )
        await proposal(id, instruction, ctx?.context_snapshot_id || null, draft)
    } catch (e) {
      update(id, { error: errorText(e), pending: false })
    } finally {
      submitting.current = false
      if (mounted.current) setBusy(false)
    }
  }
  const confirm = async (entry: Entry) => {
    update(entry.id, { pending: true, error: undefined })
    try {
      const task = await api<Task>(
        `/assistant/workflow-proposals/${entry.proposal.plan_id}/confirm`,
        { plan_hash: entry.proposal.plan_hash, confirm_complete_command: true }
      )
      update(entry.id, { proposal: { ...entry.proposal, state: "consumed" } })
      await watch(entry.id, task)
    } catch (e) {
      update(entry.id, { pending: false, error: errorText(e) })
    }
  }
  const resume = async (entry: Entry) => {
    try {
      update(entry.id, { error: undefined })
      await watch(entry.id, await api(`/assistant/tasks/${entry.task.task_id}`))
    } catch (e) {
      update(entry.id, { error: errorText(e) })
    }
  }
  const cancel = async (entry: Entry) => {
    try {
      const task = await api<Task>(
        `/assistant/tasks/${entry.task.task_id}/cancel`,
        { expected_version: entry.task.version }
      )
      update(entry.id, { task })
    } catch (e) {
      update(entry.id, { error: errorText(e) })
    }
  }
  const answer = async (entry: Entry, values: Record<string, any>) => {
    update(entry.id, { pending: true, error: undefined })
    try {
      if (values.context_snapshot_id) {
        if (!selection) throw new Error("Select a thread first.")
        values.context_snapshot_id = (
          await capture(selection)
        ).context_snapshot_id
      }
      const q = entry.task.question
      const task = await api<Task>(
        `/assistant/tasks/${entry.task.task_id}/${entry.task.scheduling ? "scheduling-inputs" : "inputs"}`,
        {
          schema_version: "1.0",
          request_id: requestId(),
          expected_version: q.expected_version,
          question_id: q.question_id,
          answer: values
        }
      )
      await watch(entry.id, task)
    } catch (e) {
      update(entry.id, { pending: false, error: errorText(e) })
    }
  }
  const loadTask = async (task: Task) => {
    const id =
      entries.find((e) => e.task?.task_id === task.task_id)?.id || task.task_id
    setEntries((old) =>
      old.some((e) => e.task?.task_id === task.task_id)
        ? old
        : [...old, { id, instruction: task.instruction, task }]
    )
    await watch(id, task)
  }
  const replace = (entryId: string, a: Artifact) =>
    setEntries((old) =>
      old.map((e) =>
        e.id === entryId
          ? {
              ...e,
              artifacts: e.artifacts?.map((x) =>
                x.task_id === a.task_id &&
                x.artifact.kind === a.artifact.kind &&
                (x.stream_key || "main") === (a.stream_key || "main") &&
                ["draft", "plan"].includes(a.artifact.kind)
                  ? a
                  : x.artifact_id === a.artifact_id
                    ? a
                    : x
              )
            }
          : e
      )
    )
  return {
    entries,
    selection,
    setSelection,
    busy,
    error,
    setError,
    preferences,
    setPreferences,
    selectActive,
    selectThread,
    submit,
    confirm,
    resume,
    cancel,
    answer,
    loadTask,
    replace,
    newChat: () => {
      if (!busy) setEntries([])
    }
  }
}
