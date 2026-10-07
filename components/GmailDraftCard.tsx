import { useEffect, useRef, useState } from "react"

import { addresses, bridge, errorText, requestId } from "../lib/api"
import type { Artifact, EmailDraft, User } from "../lib/types"
import { GmailIcon, Icon } from "./Icon"

export function GmailDraftCard({
  artifact,
  draft,
  user,
  conversation,
  enabled = true,
  replyThreadId
}: {
  artifact?: Artifact
  draft?: EmailDraft
  user: User
  conversation?: { id: string; version: number }
  enabled?: boolean
  /** The Gmail thread this chat is about; Insert targets its reply box. */
  replyThreadId?: string | null
}) {
  const content = artifact?.artifact.content || draft
  const envelope = artifact?.draft_envelope
  const insertThread = (
    envelope?.reply?.gmail_thread_id ||
    replyThreadId ||
    ""
  ).toLowerCase()
  // A reply already has its thread and subject, so the card shows only who it's
  // for and the message, and goes into Gmail with Insert rather than as a draft.
  const isReply =
    Boolean(envelope?.reply) || /^re:/i.test(content.subject || "")
  const source = artifact
    ? `task/${artifact.task_id}`
    : `conversation/${draft.draft_id}`
  const [to, setTo] = useState(
    envelope?.to?.join(", ") || draft?.recipient || ""
  )
  const [cc, setCc] = useState(envelope?.cc?.join(", ") || "")
  const [bcc, setBcc] = useState(envelope?.bcc?.join(", ") || "")
  const [subject, setSubject] = useState(content.subject)
  const [body, setBody] = useState(content.body)
  const [more, setMore] = useState(Boolean(cc || bcc))
  const [capabilities, setCapabilities] = useState<any>(null)
  const [receipt, setReceipt] = useState<any>(null)
  const [checking, setChecking] = useState(true)
  const [busy, setBusy] = useState(false)
  const [creating, setCreating] = useState(false)
  const [consenting, setConsenting] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [notice, setNotice] = useState("")
  const [error, setError] = useState("")
  const [replyOpen, setReplyOpen] = useState(false)
  const live = useRef(false)
  const submitting = useRef(false)
  const attempt = useRef<any>(null)
  const allowed = useRef(enabled)
  allowed.current = enabled
  const request = (path: string, payload?: unknown) =>
    bridge<any>({
      type: "API",
      path,
      body: payload,
      method: payload === undefined ? "GET" : "POST",
      expectedUserId: user.id
    })
  const acceptReceipt = (value: any) => {
    setReceipt(value)
    if (
      value &&
      (!artifact || value.source_artifact_id === artifact.artifact_id)
    ) {
      setTo(value.preview.to.join(", "))
      setCc(value.preview.cc.join(", "))
      setBcc(value.preview.bcc.join(", "))
      setMore(Boolean(value.preview.cc.length || value.preview.bcc.length))
      setSubject(value.preview.subject)
      setBody(value.preview.body)
      setUncertain(false)
    }
  }
  useEffect(() => {
    live.current = true
    void Promise.all([
      request("/assistant/capabilities"),
      request(`/assistant/gmail-drafts/${source}`)
    ])
      .then(([caps, saved]) => {
        if (!live.current) return
        setCapabilities(caps)
        acceptReceipt(saved)
        setChecking(false)
      })
      .catch((e) => {
        if (live.current) {
          setError(errorText(e))
          setChecking(false)
        }
      })
    return () => {
      live.current = false
    }
  }, [])
  // Insert is offered while Gmail shows a reply box on this card's email.
  useEffect(() => {
    if (!insertThread || !chrome.tabs?.query) return
    let active = true
    const check = async () => {
      let open = false
      try {
        const [tab] = await chrome.tabs.query({
          active: true,
          lastFocusedWindow: true
        })
        if (tab?.id && tab.url?.startsWith("https://mail.google.com/")) {
          const seen = await chrome.tabs.sendMessage(tab.id, {
            action: "THREADLY_SELECTION"
          })
          open =
            seen?.replyEditorOpen === true && seen.threadId === insertThread
        }
      } catch {
        open = false
      }
      if (active) setReplyOpen(open)
    }
    void check()
    const timer = setInterval(() => void check(), 1000)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [insertThread])
  const insert = async () => {
    if (submitting.current) return
    setError("")
    setNotice("")
    try {
      const [tab] = await chrome.tabs.query({
        active: true,
        lastFocusedWindow: true
      })
      if (!tab?.id) throw new Error("Open the Gmail tab with this email.")
      const result = await chrome.tabs.sendMessage(tab.id, {
        action: "THREADLY_INSERT",
        threadId: insertThread,
        body
      })
      if (!result?.ok)
        throw new Error(result?.message || "Could not insert the draft.")
      if (live.current)
        setNotice("Added to your Gmail reply. Nothing was sent.")
    } catch (e) {
      if (live.current) setError(errorText(e))
    }
  }
  const refresh = async () => {
    if (submitting.current) return
    submitting.current = true
    setBusy(true)
    setError("")
    try {
      const [caps, saved] = await Promise.all([
        request("/assistant/capabilities"),
        request(`/assistant/gmail-drafts/${source}`)
      ])
      if (!live.current) return
      setCapabilities(caps)
      acceptReceipt(saved)
      if (!saved && uncertain)
        setNotice(
          "No saved result yet. Retry the same save to recover its result safely."
        )
    } catch (e) {
      if (live.current) setError(errorText(e))
    } finally {
      submitting.current = false
      if (live.current) setBusy(false)
    }
  }
  const enableDrafts = async () => {
    if (submitting.current || !allowed.current || receipt || uncertain) return
    submitting.current = true
    setBusy(true)
    setConsenting(true)
    setError("")
    setNotice("")
    try {
      await bridge({
        type: "LOGIN",
        capabilities: ["gmail_draft"],
        expectedUserId: user.id
      })
      if (!live.current) return
      const [caps, saved] = await Promise.all([
        request("/assistant/capabilities"),
        request(`/assistant/gmail-drafts/${source}`)
      ])
      if (!live.current) return
      setCapabilities(caps)
      acceptReceipt(saved)
      setNotice(
        caps.capabilities?.some((c: any) => c.id === "gmail_draft" && c.ready)
          ? "Draft access is ready. Review your edits, then click Create draft."
          : "Google hasn’t confirmed draft permission. Your edits are preserved."
      )
    } catch (e) {
      if (live.current) setError(errorText(e))
    } finally {
      submitting.current = false
      if (live.current) {
        setBusy(false)
        setConsenting(false)
      }
    }
  }
  const create = async () => {
    if (submitting.current || !allowed.current || receipt) return
    try {
      if (!attempt.current) {
        const recipients = {
          to: addresses(to),
          cc: addresses(cc),
          bcc: addresses(bcc)
        }
        if (!recipients.to.length)
          throw new Error(
            "Add a recipient email address to create this draft in Gmail."
          )
        const all = [...recipients.to, ...recipients.cc, ...recipients.bcc].map(
          (v) => v.toLowerCase()
        )
        if (new Set(all).size !== all.length)
          throw new Error("Remove duplicate recipients from To, Cc and Bcc.")
        if (all.length > 20) throw new Error("Use no more than 20 recipients.")
        if (!subject.trim() || !body.trim())
          throw new Error(
            "Add a subject and message before creating the draft."
          )
        attempt.current = {
          request_id: requestId(),
          expected_revision: artifact?.revision || 1,
          ...(artifact
            ? { artifact_id: artifact.artifact_id }
            : { draft_id: draft.draft_id }),
          ...(conversation
            ? {
                conversation_id: conversation.id,
                expected_version: conversation.version
              }
            : {}),
          from_address: user.email,
          account_version: capabilities.account.account_version,
          subject,
          body,
          recipients,
          unresolved_fields: content.unresolved_fields || []
        }
      }
      submitting.current = true
      setBusy(true)
      setCreating(true)
      setError("")
      setNotice("")
      const saved = await request("/assistant/gmail-drafts", attempt.current)
      if (live.current) acceptReceipt(saved)
    } catch (e) {
      if (!live.current) return
      // Before dispatch, validation/permission failures leave the editor intact.
      // A lost response freezes this attempt: only identical replay is offered.
      if (attempt.current && (!e.status || e.status >= 500)) setUncertain(true)
      else attempt.current = null
      setError(errorText(e))
    } finally {
      submitting.current = false
      if (live.current) {
        setBusy(false)
        setCreating(false)
      }
    }
  }
  useEffect(() => {
    setError("")
  }, [to, cc, bcc, subject, body])
  useEffect(() => {
    if (receipt?.state !== "saving") return
    let current = true
    let timer: ReturnType<typeof setTimeout>
    let rounds = 0
    const poll = async () => {
      try {
        const saved = await request(`/assistant/gmail-drafts/${source}`)
        if (!current) return
        if (saved) acceptReceipt(saved)
        if (saved?.state === "saving" && ++rounds < 35)
          timer = setTimeout(poll, 3000)
      } catch {
        if (current)
          setError(
            "Couldn’t check this save. Refresh status before creating another copy."
          )
      }
    }
    timer = setTimeout(poll, 3000)
    return () => {
      current = false
      clearTimeout(timer)
    }
  }, [receipt?.state])
  const capability = capabilities?.capabilities?.find(
    (c: any) => c.id === "gmail_draft"
  )
  const canRequestDrafts =
    capabilities?.reconnect?.requestable_capabilities?.includes("gmail_draft")
  const permissionHint = !capabilities
    ? "Couldn’t verify Gmail draft access. Refresh status to try again."
    : capability?.status === "reconnect_required"
      ? "Your Google connection needs attention before you can create a Gmail draft."
      : capability?.status === "scope_missing"
        ? "This connection doesn’t have Gmail draft access. Additional Google permission is needed to save drafts."
        : capability?.status === "scope_unknown"
          ? "Gmail draft permission hasn’t been verified for this connection."
          : "Creating Gmail drafts isn’t available for this connection."
  const earlierRevision = Boolean(
    receipt && artifact && receipt.source_artifact_id !== artifact.artifact_id
  )
  const locked = busy || (Boolean(receipt) && !earlierRevision) || uncertain
  // Gmail Drafts controls stay for new emails, and for any reply already saved.
  const drafting = !isReply || Boolean(receipt) || uncertain
  const status = consenting
    ? "Waiting for Google…"
    : creating
      ? "Creating draft…"
      : busy
        ? "Checking status…"
        : receipt
          ? earlierRevision && receipt.state === "succeeded"
            ? "Earlier revision saved"
            : {
                succeeded: "Saved to Gmail Drafts",
                saving: "Creating draft…",
                outcome_unknown: "Waiting for confirmation",
                failed: "Draft wasn’t created"
              }[receipt.state] || "Check draft status"
          : uncertain
            ? "Save not confirmed"
            : "Not saved to Gmail"
  return (
    <section
      className="gmail-draft-card"
      aria-label="Email draft"
      aria-busy={busy || checking}>
      <header className="gmail-draft-heading">
        <span className="gmail-draft-brand">
          <GmailIcon size={22} />
          <strong>{isReply ? "Reply" : "Email draft"}</strong>
        </span>
        {(!isReply || receipt) && (
          <span
            className={`gmail-draft-status ${receipt?.state === "succeeded" ? "is-saved" : ""}`}
            role="status">
            {status}
          </span>
        )}
      </header>
      <div className="gmail-draft-fields">
        {isReply && (
          <p className="gmail-draft-from">
            <span>From</span>
            <span>{user.email}</span>
          </p>
        )}
        <label className="gmail-draft-field">
          <span>To</span>
          <input
            aria-label="To"
            autoComplete="off"
            value={to}
            disabled={locked}
            onChange={(e) => setTo(e.target.value)}
            placeholder="Name or email address"
          />
        </label>
        {!isReply && (
          <button
            className="text-button gmail-draft-more"
            type="button"
            disabled={locked}
            aria-expanded={more}
            onClick={() => setMore(!more)}>
            {more ? "Hide Cc / Bcc" : "Cc / Bcc"}
          </button>
        )}
        {more && !isReply && (
          <>
            <label className="gmail-draft-field">
              <span>Cc</span>
              <input
                aria-label="Cc"
                value={cc}
                disabled={locked}
                onChange={(e) => setCc(e.target.value)}
                placeholder="Email addresses"
              />
            </label>
            <label className="gmail-draft-field">
              <span>Bcc</span>
              <input
                aria-label="Bcc"
                value={bcc}
                disabled={locked}
                onChange={(e) => setBcc(e.target.value)}
                placeholder="Email addresses"
              />
            </label>
          </>
        )}
        {!isReply && (
          <label className="gmail-draft-field gmail-draft-subject">
            <span>Subject</span>
            <textarea
              aria-label="Subject"
              rows={2}
              maxLength={998}
              value={subject}
              disabled={locked}
              onChange={(e) =>
                setSubject(e.target.value.replace(/[\r\n]/g, " "))
              }
            />
          </label>
        )}
        <label className="gmail-draft-message">
          <span>Message</span>
          <textarea
            aria-label="Message"
            rows={8}
            maxLength={20000}
            value={body}
            disabled={locked}
            onChange={(e) => setBody(e.target.value)}
          />
        </label>
      </div>
      <footer className="gmail-draft-footer">
        {content.unresolved_fields?.length > 0 && !receipt && (
          <p className="muted">
            Before sending, fill in: {content.unresolved_fields.join("; ")}
          </p>
        )}
        <p className="gmail-draft-hint">
          {!drafting
            ? replyOpen
              ? "Edit here, then Insert it into your Gmail reply. You decide when to send."
              : "Click Reply in Gmail, then Insert this message. You decide when to send."
            : earlierRevision
              ? "An earlier revision has a Gmail save attempt. Copy these newer edits into that draft after checking its status."
              : receipt?.state === "succeeded"
                ? "Ready for you to review and send in Gmail."
                : "Edit here, then save to Gmail Drafts. You decide when to send."}
        </p>
        {drafting && !checking && !capability?.ready && !receipt && (
          <p className="warning">
            {permissionHint} You can still edit and copy this email.
          </p>
        )}
        {drafting && !capability?.ready && canRequestDrafts && !receipt && (
          <p className="gmail-draft-hint">
            Google’s permission includes managing drafts and sending email.
            Threadly uses this feature only to save drafts; you send them
            yourself in Gmail.
          </p>
        )}
        {drafting && !enabled && !receipt && (
          <p className="muted">
            This chat or draft has changed. Use the latest draft to create it in
            Gmail.
          </p>
        )}
        {receipt?.state === "outcome_unknown" && (
          <p className="warning">
            Gmail hasn’t confirmed this save. Check Gmail Drafts before creating
            another copy.
          </p>
        )}
        {receipt?.state === "failed" && (
          <p className="warning">
            Gmail rejected this save. Your text is preserved here; check your
            Google connection.
          </p>
        )}
        <div className="gmail-draft-actions">
          {drafting &&
            !capability?.ready &&
            canRequestDrafts &&
            !receipt &&
            !uncertain && (
              <button
                disabled={busy || checking || !enabled}
                onClick={() => void enableDrafts()}>
                {consenting ? "Waiting for Google…" : "Enable draft creation"}
              </button>
            )}
          {drafting && !receipt && (
            <button
              className="primary"
              disabled={busy || checking || !enabled || !capability?.ready}
              onClick={() => void create()}>
              {creating
                ? "Creating draft…"
                : uncertain
                  ? "Retry same save"
                  : "Create draft"}
            </button>
          )}
          {receipt?.state === "succeeded" && (
            <a
              className="gmail-draft-open"
              href={`https://mail.google.com/mail/?authuser=${encodeURIComponent(user.email)}#drafts`}
              target="_blank"
              rel="noreferrer">
              <Icon name="external" size={14} />
              Open Gmail Drafts
            </a>
          )}
          {insertThread && (
            <button
              className={drafting ? undefined : "primary"}
              disabled={busy || !replyOpen || !body.trim()}
              title={
                replyOpen
                  ? "Add this message to your Gmail reply"
                  : "Click Reply in Gmail to insert this message"
              }
              onClick={() => void insert()}>
              <Icon name="edit" size={14} />
              Insert
            </button>
          )}
          <button
            disabled={busy}
            onClick={() =>
              void navigator.clipboard
                .writeText(isReply ? body : `Subject: ${subject}\n\n${body}`)
                .then(() => {
                  if (live.current)
                    setNotice(
                      isReply
                        ? "Copied message."
                        : "Copied subject and message."
                    )
                })
                .catch((e) => {
                  if (live.current) setError(errorText(e))
                })
            }>
            <Icon name="copy" size={14} />
            Copy
          </button>
          {!busy &&
            drafting &&
            (receipt ||
              uncertain ||
              error ||
              (!checking && !capability?.ready)) && (
              <button onClick={() => void refresh()}>Refresh status</button>
            )}
        </div>
        {error && (
          <p role="alert" className="warning">
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="muted">
            {notice}
          </p>
        )}
      </footer>
    </section>
  )
}
