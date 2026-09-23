import { useCallback, useEffect, useRef, useState } from "react"

import { api, errorText, requestId } from "./api"
import { activeGmail, capture } from "./context"
import { chatAnswer, isRefinement, refinement } from "./conversation"
import type {
  Artifact,
  Entry,
  InboxPage,
  InboxResult,
  Selection,
  Task,
  User
} from "./types"

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
    [error, setError] = useState("")
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
  const selectActive = async (quiet = false) => {
    setBusy(true)
    setError("")
    try {
      const s = await activeGmail(user)
      if (mounted.current) {
        setSelection(s)
        if (!s && !quiet)
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
  const chooseEmail = async (mail: InboxResult) => {
    if (busy) return
    setBusy(true)
    setError("")
    try {
      const data = await api(`/threads/${encodeURIComponent(mail.thread_id)}`)
      if (!data.messages.some((m) => m.gmail_msg_id === mail.message_id))
        throw new Error(
          "That email is no longer available. Search again for its current version."
        )
      if (mounted.current)
        setSelection({
          ...data,
          selectedIds: data.messages.map((m) => m.gmail_msg_id),
          targetId: mail.message_id
        })
    } catch (e) {
      setError(errorText(e))
    } finally {
      if (mounted.current) setBusy(false)
    }
  }
  const moreEmails = async (entry: Entry) => {
    if (submitting.current || !entry.inbox?.next_cursor) return
    submitting.current = true
    setBusy(true)
    update(entry.id, { pending: true, error: undefined })
    try {
      const next = await api<InboxPage>("/assistant/inbox-search-page", {
        filters: entry.inbox.filters,
        cursor: entry.inbox.next_cursor
      })
      const seen = new Set(entry.inbox.results.map((r) => r.message_id))
      update(entry.id, {
        pending: false,
        inbox: {
          ...next,
          results: [
            ...entry.inbox.results,
            ...next.results.filter((r) => !seen.has(r.message_id))
          ]
        }
      })
    } catch (e) {
      update(entry.id, { pending: false, error: errorText(e) })
    } finally {
      submitting.current = false
      if (mounted.current) setBusy(false)
    }
  }
  const proposal = async (
    id: string,
    instruction: string,
    contextId: string | null,
    draft: any
  ) => {
    // Settings can change while this conversation stays mounted. Bind fresh preferences.
    let p = null
    try {
      p = await api("/calendar/preferences")
    } catch (e) {
      if (e.code !== "calendar_preferences_missing" && e.status !== 403) throw e
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
  const submit = async (instruction: string, rewriteMessageId?: string) => {
    if (submitting.current || !instruction.trim()) return
    const last = entries.at(-1)
    const response = rewriteMessageId ? null : chatAnswer(last, instruction)
    if (response) {
      submitting.current = true
      setBusy(true)
      update(last.id, { answers: [...(last.answers || []), instruction] })
      try {
        await answer(last, response)
      } finally {
        submitting.current = false
        if (mounted.current) setBusy(false)
      }
      return
    }
    submitting.current = true
    setBusy(true)
    setError("")
    const id = requestId()
    setEntries((old) => [
      ...old,
      { id, instruction, pending: true, createdAt: new Date().toISOString() }
    ])
    try {
      if (
        rewriteMessageId &&
        (!selection ||
          selection.targetId !== rewriteMessageId ||
          !selection.selectedIds.includes(rewriteMessageId))
      )
        throw new Error(
          "Choose the message to rewrite from the email context first."
        )
      const follow = rewriteMessageId ? null : refinement(last, instruction)
      if (!rewriteMessageId && isRefinement(instruction) && !follow)
        throw new Error(
          "Which result would you like to change? Open its conversation from History, or describe the full request."
        )
      if (!follow && !rewriteMessageId) {
        const previousSearch = [...entries].reverse().find((e) => e.inbox)
        if (
          /^(?:please )?(?:show |load )?(?:me )?(?:more|next(?: emails?| page)?)(?: please)?[.!?]?$/i.test(
            instruction.trim()
          ) &&
          previousSearch
        ) {
          if (!previousSearch.inbox.next_cursor) {
            update(id, {
              pending: false,
              message:
                "There are no more results in this search. Try a different sender or date range."
            })
            return
          }
          const inbox = await api<InboxPage>("/assistant/inbox-search-page", {
            filters: previousSearch.inbox.filters,
            cursor: previousSearch.inbox.next_cursor
          })
          update(id, { pending: false, inbox })
          return
        }
        const turn = await api("/assistant/inbox-chat", {
          instruction,
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
        })
        if (turn.kind === "message") {
          update(id, { pending: false, message: turn.text })
          return
        }
        if (turn.kind === "search") {
          update(id, { pending: false, inbox: turn.search })
          return
        }
        if (turn.kind !== "continue")
          throw new Error("I couldn’t finish that request. Please try again.")
      }
      const ctx = !follow && selection ? await capture(selection) : null
      const recipe = follow || {
        instruction,
        contextId: ctx?.context_snapshot_id || null,
        hint: rewriteMessageId ? "other" : null,
        draft: null
      }
      update(id, {
        recipe,
        ...(follow
          ? {
              notice:
                "Regenerating the previous request with your wording preference and its original sources."
            }
          : {})
      })
      const task = await api<Task>("/assistant/requests", {
        schema_version: "1.0",
        request_id: id,
        instruction: recipe.instruction,
        intent_hint: recipe.hint,
        context_snapshot_id: recipe.contextId,
        continuation: null,
        ...(recipe.draft ? { draft_options: recipe.draft } : {}),
        ...(rewriteMessageId
          ? {
              read_options: {
                operation: "transform_text",
                message_id: rewriteMessageId
              }
            }
          : {})
      })
      const done = await watch(id, task)
      await proposeIfNeeded(
        id,
        done,
        recipe.instruction,
        recipe.contextId,
        recipe.draft
      )
    } catch (e) {
      update(id, { error: errorText(e), pending: false })
    } finally {
      submitting.current = false
      if (mounted.current) setBusy(false)
    }
  }
  const proposeIfNeeded = async (
    id: string,
    done: Task,
    instruction: string,
    contextId: string | null,
    draft: any
  ) => {
    const route = done?.route?.decision
    if (
      mounted.current &&
      done?.state === "unsupported" &&
      route?.requested_action === "none" &&
      (route?.operations?.length > 1 || route?.intent === "plan_schedule")
    ) {
      const envelope = done.effective_draft_input || done.draft_input
      const options = envelope
        ? {
            to: envelope.to,
            cc: envelope.cc,
            bcc: envelope.bcc,
            reply_message_id:
              envelope.reply_message_id ||
              envelope.reply?.gmail_message_id ||
              null
          }
        : draft
      await proposal(
        id,
        instruction,
        done.effective_context_snapshot_id || contextId,
        options
      )
    }
  }
  const confirm = async (entry: Entry) => {
    if (submitting.current) return
    submitting.current = true
    setBusy(true)
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
    } finally {
      submitting.current = false
      if (mounted.current) setBusy(false)
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
    setBusy(true)
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
      const done = await watch(entry.id, task)
      await proposeIfNeeded(
        entry.id,
        done,
        entry.recipe?.instruction ||
          entry.task.instruction ||
          entry.instruction,
        done.context_snapshot_id || null,
        entry.recipe?.draft || null
      )
    } catch (e) {
      update(entry.id, { pending: false, error: errorText(e) })
    } finally {
      if (mounted.current) setBusy(false)
    }
  }
  const loadTask = async (task: Task) => {
    if (submitting.current || busy) return
    setBusy(true)
    setSelection(null)
    setError("")
    const id = task.task_id
    setEntries([{ id, instruction: task.instruction, task }])
    try {
      const contextId =
        task.effective_context_snapshot_id || task.context_snapshot_id
      if (contextId) {
        const saved = await api(`/assistant/context-snapshots/${contextId}`)
        const data = await api(
          `/threads/${encodeURIComponent(saved.thread_id)}`
        )
        if (data.thread.version !== saved.thread_version)
          throw new Error(
            "This email has changed since that conversation. Attach its current version before asking another question."
          )
        const ids =
          saved.ui_map?.visible_message_ids ||
          saved.messages?.map((m) => m.message_id) ||
          []
        if (
          !ids.length ||
          !ids.every((id) => data.messages.some((m) => m.gmail_msg_id === id))
        )
          throw new Error(
            "The original email context is no longer available. Attach an email to continue."
          )
        if (mounted.current)
          setSelection({
            ...data,
            messages: ids.map((id) =>
              data.messages.find((m) => m.gmail_msg_id === id)
            ),
            selectedIds: ids,
            targetId:
              saved.ui_map?.selected_message_ids?.length === 1
                ? saved.ui_map.selected_message_ids[0]
                : null
          })
      }
    } catch (e) {
      if (mounted.current) setError(errorText(e))
    } finally {
      await watch(id, task)
      if (mounted.current) setBusy(false)
    }
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
    email: user.email,
    entries,
    selection,
    setSelection,
    busy,
    error,
    setError,
    selectActive,
    selectThread,
    chooseEmail,
    moreEmails,
    submit,
    confirm,
    resume,
    cancel,
    answer,
    loadTask,
    replace,
    newChat: () => {
      if (!busy) {
        setEntries([])
        setError("")
        setSelection(null)
        void selectActive(true)
      }
    }
  }
}
