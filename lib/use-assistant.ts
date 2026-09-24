import { useCallback, useEffect, useRef, useState } from "react"

import { api, errorText, requestId } from "./api"
import { activeGmail, capture } from "./context"
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

type ConversationTurnBody = {
  conversation_id: string
  expected_version: number
  request_id: string
  instruction: string
  timezone: string
  context_snapshot_id?: string | null
  active_task_id?: string | null
}

type PendingTurn = {
  id: string
  body: Readonly<ConversationTurnBody>
}

type SelectionReference = {
  threadId: string
  threadVersion: number
  visibleMessageIds: string[]
  selectedIds: string[]
  targetId: string | null
}

const selectionReference = (value: Selection): SelectionReference => ({
  threadId: value.thread.thread_id,
  threadVersion: value.thread.version,
  visibleMessageIds: value.messages.map((message) => message.gmail_msg_id),
  selectedIds: [...value.selectedIds],
  targetId: value.targetId
})

const validSelectionReference = (value: any): value is SelectionReference =>
  value &&
  typeof value.threadId === "string" &&
  value.threadId.length > 0 &&
  Number.isInteger(value.threadVersion) &&
  value.threadVersion >= 0 &&
  Array.isArray(value.visibleMessageIds) &&
  value.visibleMessageIds.length > 0 &&
  value.visibleMessageIds.length <= 100 &&
  value.visibleMessageIds.every(
    (id: unknown) => typeof id === "string" && id.length > 0
  ) &&
  new Set(value.visibleMessageIds).size === value.visibleMessageIds.length &&
  Array.isArray(value.selectedIds) &&
  value.selectedIds.length <= value.visibleMessageIds.length &&
  new Set(value.selectedIds).size === value.selectedIds.length &&
  value.selectedIds.every(
    (id: unknown) =>
      typeof id === "string" && value.visibleMessageIds.includes(id)
  ) &&
  (value.targetId === null ||
    (typeof value.targetId === "string" &&
      value.selectedIds.includes(value.targetId)))

type ConversationHistoryItem = {
  user: string
  assistant: string
  kind?: string
  request_id?: string | null
  task_id?: string | null
  proposal_id?: string | null
}

const pendingTurn = (id: string, body: ConversationTurnBody): PendingTurn => ({
  id,
  // A retry is an idempotent replay. Freeze a fresh copy so reconciliation
  // cannot silently change a request after it has been issued.
  body: Object.freeze({ ...body })
})

export function useAssistant(user: User) {
  const [entries, setEntries] = useState<Entry[]>([]),
    [selection, setSelection] = useState<Selection | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [contextBlocked, setContextBlocked] = useState(false),
    [restoring, setRestoring] = useState(true),
    [restoreFailed, setRestoreFailed] = useState(false)
  const mounted = useRef(true),
    polling = useRef(new Set<string>()),
    submitting = useRef(false),
    conversation = useRef({ id: requestId(), version: 0 }),
    retryTurn = useRef<PendingTurn | null>(null),
    contextCleared = useRef(false),
    contextRevision = useRef(0),
    selectionOverride = useRef<SelectionReference | null>(null),
    pinnedCapture = useRef<{ selection: Selection; id: string } | null>(null),
    storageQueue = useRef<Promise<void>>(Promise.resolve())
  const storageKey = `threadlyConversation:${user.id}`
  const queueStorage = (write: () => Promise<void> | undefined) => {
    const next = storageQueue.current
      .catch(() => undefined)
      .then(async () => {
        await write()
      })
    storageQueue.current = next
    return next
  }
  const saveConversation = (pendingTurn = retryTurn.current) => {
    const saved = {
      ...conversation.current,
      pendingTurn,
      contextCleared: contextCleared.current,
      selectionOverride: selectionOverride.current
    }
    return queueStorage(() =>
      chrome.storage.session?.set({ [storageKey]: saved })
    )
  }
  const forgetConversation = () =>
    queueStorage(() => chrome.storage.session?.remove(storageKey))
  const persistContextChoice = () => {
    void saveConversation().catch((e) => {
      if (mounted.current)
        setError(
          `Email context could not be saved in this browser. ${errorText(e)}`
        )
    })
  }
  const markContextAttached = (value: Selection) => {
    contextRevision.current += 1
    contextCleared.current = false
    // Keep only provider IDs and the chosen order, never email content. The
    // server pin remains authoritative once a turn commits this selection.
    selectionOverride.current = selectionReference(value)
    persistContextChoice()
  }
  useEffect(() => {
    let alive = true
    const restoreLocalSelection = async (reference: unknown) => {
      const revision = contextRevision.current
      if (!validSelectionReference(reference))
        throw new Error(
          "The selected email reference is invalid. Attach it again."
        )
      const data = await api(
        `/threads/${encodeURIComponent(reference.threadId)}`
      )
      if (!alive || submitting.current || contextRevision.current !== revision)
        return
      if (data.thread.version !== reference.threadVersion)
        throw new Error(
          "The selected email changed. Attach its current version."
        )
      const messages = reference.visibleMessageIds.map((id) =>
        data.messages.find((message: any) => message.gmail_msg_id === id)
      )
      if (messages.some((message) => !message))
        throw new Error(
          "The selected email changed. Attach its current version."
        )
      selectionOverride.current = reference
      setSelection({
        ...data,
        messages,
        selectedIds: reference.selectedIds,
        targetId: reference.targetId
      })
      setContextBlocked(false)
    }
    const restore = async () => {
      const saved = (await chrome.storage.session?.get(storageKey))?.[
        storageKey
      ]
      if (!saved || !alive || submitting.current) return
      let value: any
      try {
        value = await api(`/assistant/conversations/${saved.id}`)
      } catch (e) {
        if (e.status === 404 && saved.version === 0 && !saved.pendingTurn) {
          conversation.current = { id: saved.id, version: 0 }
          contextCleared.current = saved.contextCleared === true
          setSelection(null)
          if (saved.selectionOverride)
            try {
              await restoreLocalSelection(saved.selectionOverride)
            } catch (selectionError) {
              setContextBlocked(true)
              setError(errorText(selectionError))
            }
          return
        }
        if (
          e.status === 404 &&
          saved.pendingTurn?.body?.expected_version === 0
        ) {
          conversation.current = { id: saved.id, version: saved.version || 0 }
          retryTurn.current = pendingTurn(
            saved.pendingTurn.id,
            saved.pendingTurn.body
          )
          setEntries([
            {
              id: saved.pendingTurn.id,
              instruction: saved.pendingTurn.body.instruction,
              error: "This response was interrupted. Retry it safely."
            }
          ])
          if (saved.selectionOverride)
            try {
              await restoreLocalSelection(saved.selectionOverride)
            } catch (selectionError) {
              setContextBlocked(true)
              setError(errorText(selectionError))
            }
          return
        }
        if (e.status === 404 || e.code === "conversation_account_changed")
          await forgetConversation()
        else if (alive) {
          setRestoreFailed(true)
          setError(`Conversation could not be restored. ${errorText(e)}`)
        }
        return
      }
      try {
        if (!alive || submitting.current) return
        conversation.current = {
          id: value.conversation_id,
          version: Math.max(saved.version || 0, value.version)
        }
        // A detached pin is a local choice until the next turn commits an
        // explicit null. Keep it detached across side-panel reopen, but let a
        // newer server conversation version take precedence.
        contextCleared.current =
          saved.contextCleared === true && value.version <= saved.version
        selectionOverride.current =
          value.version <= saved.version &&
          validSelectionReference(saved.selectionOverride)
            ? saved.selectionOverride
            : null
        const history: ConversationHistoryItem[] = value.history || []
        const restoredEntries: Entry[] = history.map((h, i) => ({
          id: h.request_id || `restored-${i}`,
          instruction: h.user,
          message: h.assistant
        }))
        const pending = saved.pendingTurn
          ? pendingTurn(saved.pendingTurn.id, saved.pendingTurn.body)
          : null
        if (pending) {
          const matchingRequest = history.find(
            (h) => h.request_id === pending.body.request_id
          )
          const last = history.at(-1)
          // Older servers omitted request_id. Only use the last text as a
          // fallback when no history item has an ID and exactly one turn landed.
          const legacyMatch =
            !history.some((h) => h.request_id) &&
            value.version === pending.body.expected_version + 1 &&
            last?.user === pending.body.instruction
          const completed = Boolean(matchingRequest || legacyMatch)
          if (completed) {
            retryTurn.current = null
            try {
              await saveConversation(null)
            } catch (e) {
              setError(
                `The restored chat could not be saved locally. ${errorText(e)}`
              )
            }
          } else if (
            (value.pending_request_id &&
              value.pending_request_id !== pending.body.request_id) ||
            (!value.pending_request_id &&
              value.version > pending.body.expected_version) ||
            value.version < pending.body.expected_version
          ) {
            retryTurn.current = null
            restoredEntries.push({
              id: pending.id,
              instruction: pending.body.instruction,
              error:
                "This message could not be matched to the server chat. Review the conversation before sending it again."
            })
            try {
              await saveConversation(null)
            } catch (e) {
              setError(
                `The restored chat could not be saved locally. ${errorText(e)}`
              )
            }
          } else {
            retryTurn.current = pending
            restoredEntries.push({
              id: pending.id,
              instruction: pending.body.instruction,
              error: "This response was interrupted. Retry it safely."
            })
            try {
              await saveConversation(pending)
            } catch (e) {
              setError(`The retry could not be saved locally. ${errorText(e)}`)
            }
          }
        }
        setEntries(restoredEntries)
        const pendingEmailWithoutSelection =
          typeof retryTurn.current?.body.context_snapshot_id === "string" &&
          !validSelectionReference(saved.selectionOverride)
        if (saved.selectionOverride && value.version <= saved.version) {
          try {
            await restoreLocalSelection(saved.selectionOverride)
          } catch (selectionError) {
            setContextBlocked(true)
            setError(errorText(selectionError))
          }
        } else if (
          value.context_snapshot_id &&
          !contextCleared.current &&
          !pendingEmailWithoutSelection
        ) {
          // Keep submission blocked while the saved pin is being validated.
          setContextBlocked(true)
          const revision = contextRevision.current
          try {
            const source = await api(
              `/assistant/context-snapshots/${value.context_snapshot_id}`
            )
            const data = await api(
              `/threads/${encodeURIComponent(source.thread_id)}`
            )
            if (
              !alive ||
              submitting.current ||
              contextRevision.current !== revision
            )
              return
            if (data.thread.version === source.thread_version) {
              const selectedIds =
                source.ui_map?.visible_message_ids ||
                data.messages.map((m) => m.gmail_msg_id)
              const restored = {
                ...data,
                selectedIds,
                targetId: source.ui_map?.selected_message_ids?.[0] || null
              }
              setSelection(restored)
              setContextBlocked(false)
              contextCleared.current = false
              pinnedCapture.current = {
                selection: restored,
                id: value.context_snapshot_id
              }
            } else {
              setContextBlocked(true)
              setError(
                "The pinned email changed. Attach its current version or continue without it before sending another message."
              )
            }
          } catch (e) {
            if (alive) {
              setContextBlocked(true)
              setError(
                "This conversation was restored, but its pinned email is unavailable. " +
                  `${errorText(e)} Attach another email or continue without it.`
              )
            }
          }
        }
        if (value.active_task_id) {
          const task = await api<Task>(
            `/assistant/tasks/${value.active_task_id}`
          )
          if (!alive || submitting.current) return
          const taskIndex = history.findIndex(
            (item) => item.task_id === value.active_task_id
          )
          const id =
            taskIndex >= 0
              ? restoredEntries[taskIndex].id
              : `restored-task-${value.active_task_id}`
          setEntries((old) =>
            old.some((entry) => entry.id === id)
              ? old.map((entry) =>
                  entry.id === id ? { ...entry, task } : entry
                )
              : [...old, { id, instruction: task.instruction, task }]
          )
          void watch(id, task)
        }
        if (value.active_proposal_id) {
          const proposal = await api(
            `/assistant/workflow-proposals/${value.active_proposal_id}`
          )
          if (!alive || submitting.current) return
          const proposalIndex = history.findIndex(
            (item) => item.proposal_id === value.active_proposal_id
          )
          const id =
            proposalIndex >= 0
              ? restoredEntries[proposalIndex].id
              : `restored-proposal-${value.active_proposal_id}`
          setEntries((old) =>
            old.some((entry) => entry.id === id)
              ? old.map((entry) =>
                  entry.id === id ? { ...entry, proposal } : entry
                )
              : [
                  ...old,
                  {
                    id,
                    instruction:
                      proposal.request?.instruction || "Review these steps",
                    proposal
                  }
                ]
          )
        }
      } catch (e) {
        if (alive) {
          setRestoreFailed(true)
          setError(`Conversation could not be fully restored. ${errorText(e)}`)
        }
      }
    }
    void restore()
      .catch((e) => {
        if (alive) {
          setRestoreFailed(true)
          setError(`Conversation could not be restored. ${errorText(e)}`)
        }
      })
      .finally(() => {
        if (alive) setRestoring(false)
      })
    return () => {
      alive = false
    }
  }, [user.id])
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
    if (retryTurn.current) {
      if (!quiet)
        setError(
          "Retry the unfinished message or start a new conversation before changing its email."
        )
      return
    }
    if (quiet && (await chrome.storage.session?.get(storageKey))?.[storageKey])
      return
    setBusy(true)
    setError("")
    try {
      const s = await activeGmail(user)
      if (mounted.current) {
        if (s) {
          setSelection(s)
          setContextBlocked(false)
          markContextAttached(s)
          pinnedCapture.current = null
        } else if (!quiet)
          setError(
            selection
              ? "No open Gmail email was found, so the current pinned email was kept."
              : "Open an email in Gmail, or choose a thread from Search mail."
          )
      }
    } catch (e) {
      if (mounted.current) setError(errorText(e))
    } finally {
      if (mounted.current) setBusy(false)
    }
  }
  const selectThread = async (id: string) => {
    if (retryTurn.current) {
      setError(
        "Retry the unfinished message or start a new conversation before changing its email."
      )
      return
    }
    setBusy(true)
    setError("")
    try {
      const data = await api(`/threads/${encodeURIComponent(id)}`)
      if (mounted.current) {
        setContextBlocked(false)
        pinnedCapture.current = null
        const chosen = {
          ...data,
          selectedIds: data.messages.map((m) => m.gmail_msg_id),
          targetId: null
        }
        markContextAttached(chosen)
        setSelection(chosen)
      }
    } catch (e) {
      if (mounted.current) setError(errorText(e))
    } finally {
      if (mounted.current) setBusy(false)
    }
  }
  const chooseEmail = async (mail: InboxResult) => {
    if (busy) return
    if (retryTurn.current) {
      setError(
        "Retry the unfinished message or start a new conversation before changing its email."
      )
      return
    }
    setBusy(true)
    setError("")
    try {
      const data = await api(`/threads/${encodeURIComponent(mail.thread_id)}`)
      if (!data.messages.some((m) => m.gmail_msg_id === mail.message_id))
        throw new Error(
          "That email is no longer available. Search again for its current version."
        )
      if (mounted.current) {
        const chosen = data.messages.find(
          (m) => m.gmail_msg_id === mail.message_id
        )
        setContextBlocked(false)
        pinnedCapture.current = null
        const selection = {
          ...data,
          messages: [chosen],
          selectedIds: [mail.message_id],
          targetId: mail.message_id
        }
        markContextAttached(selection)
        setSelection(selection)
      }
    } catch (e) {
      setError(errorText(e))
    } finally {
      if (mounted.current) setBusy(false)
    }
  }
  const activeSearch = () => [...entries].reverse().find((e) => e.inbox)
  const canPage = (entry: Entry) =>
    !retryTurn.current &&
    !contextBlocked &&
    activeSearch()?.id === entry.id &&
    Boolean(entry.inbox?.next_cursor)
  const moreEmails = async (entry: Entry) => {
    if (!canPage(entry)) {
      setError(
        "Only the newest email search can load another page. Run that search again to continue it."
      )
      return
    }
    await submit("Show me the next page of these emails")
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
    if (restoreFailed) {
      setError(
        "Reopen Threadly to retry loading this conversation, or start a new conversation."
      )
      return
    }
    if (!rewriteMessageId && contextBlocked) {
      setError(
        "Resolve the unavailable email context first: attach another email or continue without it."
      )
      return
    }
    if (retryTurn.current) {
      setError("Retry the unfinished message, or start a new conversation.")
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
      if (!rewriteMessageId) {
        // The server owns semantic interpretation and conversation memory. The UI sends
        // references and the actual new user turn, never an invented combined instruction.
        let contextId = null
        if (selection) {
          if (pinnedCapture.current?.selection !== selection) {
            const value = await capture(selection)
            pinnedCapture.current = { selection, id: value.context_snapshot_id }
          }
          contextId = pinnedCapture.current.id
        }
        const lastWork = [...entries]
          .reverse()
          .find(
            (e) => e.task || (e.proposal && e.proposal.state !== "consumed")
          )
        const activeProposal =
          lastWork?.proposal && lastWork.proposal.state !== "consumed"
        const body = {
          conversation_id: conversation.current.id,
          expected_version: conversation.current.version,
          request_id: id,
          instruction,
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
          ...(contextId || contextCleared.current
            ? { context_snapshot_id: contextId }
            : {}),
          ...(activeProposal
            ? {}
            : { active_task_id: lastWork?.task?.task_id || null })
        }
        retryTurn.current = pendingTurn(id, body)
        await saveConversation(retryTurn.current)
        await sendTurn(id, body)
        return
      }
      // Explicit context-menu rewrite remains a typed action, not conversational routing.
      const ctx = await capture(selection)
      const recipe = {
        instruction,
        contextId: ctx.context_snapshot_id,
        hint: "other",
        draft: null
      }
      update(id, { recipe })
      const task = await api<Task>("/assistant/requests", {
        schema_version: "1.0",
        request_id: id,
        instruction,
        intent_hint: "other",
        context_snapshot_id: ctx.context_snapshot_id,
        continuation: null,
        read_options: {
          operation: "transform_text",
          message_id: rewriteMessageId
        }
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
      if (!rewriteMessageId && retryTurn.current?.id === id)
        await reconcileTurnError(id, e)
      else update(id, { error: errorText(e), pending: false })
    } finally {
      submitting.current = false
      if (mounted.current) setBusy(false)
    }
  }
  const sendTurn = async (id: string, body: Readonly<ConversationTurnBody>) => {
    const turn = await api("/assistant/conversation-turns", body)
    if (
      turn.conversation_id !== body.conversation_id ||
      turn.version !== body.expected_version + 1
    )
      throw new Error(
        "The server returned an inconsistent conversation version."
      )
    conversation.current = {
      id: turn.conversation_id,
      version: Math.max(conversation.current.version, turn.version)
    }
    if ("context_snapshot_id" in body) {
      contextCleared.current = false
      selectionOverride.current = null
    }
    retryTurn.current = null
    update(id, {
      pending: false,
      message: turn.text,
      inbox: turn.search,
      task: turn.task,
      proposal: turn.proposal,
      notice: turn.notice,
      evidence: turn.evidence,
      artifacts: turn.artifacts,
      conversationVersion: turn.version,
      error: undefined
    })
    try {
      await saveConversation(null)
    } catch (e) {
      update(id, {
        notice: [
          turn.notice,
          `This response completed, but the conversation could not be saved in this browser. Keep this panel open while you use it. ${errorText(e)}`
        ]
          .filter(Boolean)
          .join(" ")
      })
    }
    if (turn.task) {
      void watch(id, turn.task).then(async (done) => {
        try {
          await proposeIfNeeded(
            id,
            done,
            done.instruction,
            done.effective_context_snapshot_id ||
              done.context_snapshot_id ||
              null,
            null
          )
        } catch (e) {
          update(id, { error: errorText(e), pending: false })
        }
      })
    }
  }
  const reconcileTurnError = async (id: string, failure: any) => {
    let message = errorText(failure)
    const pending = retryTurn.current
    if (
      failure?.code === "conversation_version_conflict" &&
      pending?.id === id
    ) {
      try {
        const latest = await api(
          `/assistant/conversations/${pending.body.conversation_id}`
        )
        conversation.current = {
          id: latest.conversation_id,
          version: Math.max(conversation.current.version, latest.version)
        }
        const completed = latest.history?.some(
          (item: ConversationHistoryItem) =>
            item.request_id === pending.body.request_id
        )
        if (completed) {
          message =
            "This response finished on the server. Retry response to restore its result."
        } else if (
          latest.pending_request_id &&
          latest.pending_request_id !== pending.body.request_id
        ) {
          retryTurn.current = null
          message =
            "Another unfinished message owns this conversation. Review the chat or start a new one."
          try {
            await saveConversation(null)
          } catch (e) {
            message += ` The updated chat could not be saved locally. ${errorText(e)}`
          }
        } else if (latest.pending_request_id === pending.body.request_id)
          message = "This response is still being prepared. Retry it shortly."
        else {
          // Never rebase an issued request under the same idempotency key.
          retryTurn.current = null
          message =
            "This chat changed in another panel, so this message was not applied. Send it again to use the latest conversation."
          try {
            await saveConversation(null)
          } catch (e) {
            message += ` The updated chat could not be saved locally. ${errorText(e)}`
          }
        }
      } catch (e) {
        message = `${message} ${errorText(e)}`
        if (e?.status === 404 || e?.code === "conversation_account_changed") {
          retryTurn.current = null
          message += " Start a new conversation to continue."
          try {
            await saveConversation(null)
          } catch (storageError) {
            message += ` Browser storage could not be updated. ${errorText(storageError)}`
          }
        }
      }
    } else if (
      pending?.id === id &&
      ((failure?.status === 404 && pending.body.expected_version > 0) ||
        failure?.status === 422 ||
        ["conversation_account_changed", "idempotency_conflict"].includes(
          failure?.code
        ))
    ) {
      retryTurn.current = null
      message += " Start a new conversation to continue."
      try {
        await saveConversation(null)
      } catch (e) {
        message += ` Browser storage could not be updated. ${errorText(e)}`
      }
    }
    update(id, { error: message, pending: false })
  }
  const retry = async (entry: Entry) => {
    if (submitting.current || retryTurn.current?.id !== entry.id) return
    submitting.current = true
    setBusy(true)
    update(entry.id, { pending: true, error: undefined })
    try {
      await saveConversation(retryTurn.current)
      await sendTurn(entry.id, retryTurn.current.body)
    } catch (e) {
      await reconcileTurnError(entry.id, e)
    } finally {
      submitting.current = false
      setBusy(false)
    }
  }
  const deleteChat = async () => {
    if (busy || submitting.current) return
    submitting.current = true
    setBusy(true)
    try {
      // A timed-out first turn may have created the row while the local version
      // is still zero. DELETE is owner-scoped and idempotent server-side.
      await api(
        `/assistant/conversations/${conversation.current.id}`,
        undefined,
        "DELETE"
      )
      let storageError = ""
      try {
        await forgetConversation()
      } catch (e) {
        storageError = `The chat was deleted on the server, but browser storage could not be cleared. ${errorText(e)}`
      }
      conversation.current = { id: requestId(), version: 0 }
      retryTurn.current = null
      contextRevision.current += 1
      selectionOverride.current = null
      setRestoreFailed(false)
      pinnedCapture.current = null
      contextCleared.current = false
      setContextBlocked(false)
      setEntries([])
      setError(storageError)
      setSelection(null)
    } catch (e) {
      setError(errorText(e))
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
    submitting.current = true
    setBusy(true)
    const id = task.task_id
    try {
      setSelection(null)
      setContextBlocked(false)
      setError("")
      conversation.current = { id: requestId(), version: 0 }
      retryTurn.current = null
      contextRevision.current += 1
      selectionOverride.current = null
      pinnedCapture.current = null
      contextCleared.current = false
      await forgetConversation()
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
      }
      await watch(id, task)
    } catch (e) {
      if (mounted.current) setError(errorText(e))
    } finally {
      submitting.current = false
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
    setSelection: (value: Selection | null) => {
      if (retryTurn.current) {
        setError(
          "Retry the unfinished message or start a new conversation before changing its email."
        )
        return
      }
      if (value) markContextAttached(value)
      else {
        contextRevision.current += 1
        contextCleared.current = true
        selectionOverride.current = null
      }
      pinnedCapture.current = null
      setContextBlocked(false)
      setSelection(value)
      if (!value) persistContextChoice()
    },
    contextBlocked,
    clearContext: () => {
      if (retryTurn.current) {
        setError(
          "Retry the unfinished message or start a new conversation before detaching its email."
        )
        return
      }
      contextRevision.current += 1
      contextCleared.current = true
      selectionOverride.current = null
      pinnedCapture.current = null
      setContextBlocked(false)
      setSelection(null)
      setError("")
      persistContextChoice()
    },
    busy,
    restoring,
    restoreFailed,
    contextLocked: Boolean(retryTurn.current),
    pendingUsesHiddenEmail:
      typeof retryTurn.current?.body.context_snapshot_id === "string" &&
      !selection,
    error,
    setError,
    selectActive,
    selectThread,
    chooseEmail,
    canPage,
    moreEmails,
    submit,
    retry,
    canRetry: (entry: Entry) => retryTurn.current?.id === entry.id,
    deleteChat,
    confirm,
    resume,
    cancel,
    answer,
    loadTask,
    replace,
    newChat: async () => {
      if (!busy && !submitting.current) {
        try {
          submitting.current = true
          await forgetConversation()
        } catch (e) {
          setError(errorText(e))
          return
        } finally {
          submitting.current = false
        }
        conversation.current = { id: requestId(), version: 0 }
        retryTurn.current = null
        contextRevision.current += 1
        selectionOverride.current = null
        setRestoreFailed(false)
        pinnedCapture.current = null
        contextCleared.current = false
        setContextBlocked(false)
        setEntries([])
        setError("")
        setSelection(null)
        await selectActive(true)
      }
    }
  }
}
