import { useEffect, useRef, useState } from "react"

import { api, errorText } from "../lib/api"
import { Icon } from "./Icon"

type Setting = { mode: "ask" | "always"; version: number }
type Props = {
  conversationId: string
  disabled?: boolean
  remember: () => Promise<unknown>
  onSaving: (saving: boolean) => void
}
export function CalendarApprovalMenu(props: Props) {
  // A new chat owns a new UI instance; an old save can never change its display.
  return <ChatCalendarApprovalMenu key={props.conversationId} {...props} />
}
function ChatCalendarApprovalMenu({
  conversationId,
  disabled,
  remember,
  onSaving
}: Props) {
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      onSaving(false)
    }
  }, [onSaving])
  const [setting, setSetting] = useState<Setting | null>(null)
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")
  const root = useRef<HTMLDivElement>(null),
    trigger = useRef<HTMLButtonElement>(null)
  const apply = (next: Setting) => {
    if (alive.current)
      setSetting((current) =>
        current && current.version > next.version ? current : next
      )
  }
  const path = `/assistant/conversations/${conversationId}/calendar-approval`
  useEffect(() => {
    let live = true
    setSetting(null)
    setOpen(false)
    setError("")
    api<Setting>(path)
      .then((s) => {
        if (live) apply(s)
      })
      .catch(() => {
        if (live)
          setError("Couldn't load this chat's approval setting. Open to retry.")
      })
    return () => {
      live = false
    }
  }, [path])
  useEffect(() => {
    if (!open) return
    const dismiss = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("pointerdown", dismiss)
    root.current
      ?.querySelector<HTMLButtonElement>(
        '[role="menuitemradio"][aria-checked="true"]'
      )
      ?.focus()
    return () => document.removeEventListener("pointerdown", dismiss)
  }, [open])
  const choose = async (mode: Setting["mode"]) => {
    if (!setting || saving) return
    setSaving(true)
    onSaving(true)
    setError("")
    try {
      // Persist the chat pointer before granting a permission tied to that chat.
      await remember()
      if (!alive.current) return
      apply(
        await api<Setting>(
          path,
          { mode, expected_version: setting.version },
          "PUT"
        )
      )
      if (!alive.current) return
      setOpen(false)
      trigger.current?.focus()
    } catch (e) {
      if (!alive.current) return
      setError(errorText(e))
      // A lost response may have committed; reread instead of assuming success/failure.
      try {
        apply(await api<Setting>(path))
      } catch {
        if (alive.current) setSetting(null)
      }
    } finally {
      if (alive.current) {
        setSaving(false)
        onSaving(false)
      }
    }
  }
  return (
    <div
      className="calendar-permission"
      ref={root}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          setOpen(false)
          trigger.current?.focus()
        }
        if (open && ["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
          e.preventDefault()
          const items = [
            ...root.current!.querySelectorAll<HTMLButtonElement>(
              '[role="menuitemradio"]'
            )
          ]
          const i = items.indexOf(document.activeElement as HTMLButtonElement)
          items[
            e.key === "Home"
              ? 0
              : e.key === "End"
                ? items.length - 1
                : (i + (e.key === "ArrowUp" ? -1 : 1) + items.length) %
                  items.length
          ]?.focus()
        }
      }}>
      <button
        ref={trigger}
        type="button"
        className={`icon-button${setting?.mode === "always" ? " permission-active" : ""}`}
        aria-label={`Calendar approval: ${setting ? (setting.mode === "always" ? "Always allow" : "Ask for approval") : "unavailable"}`}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Calendar approval"
        disabled={disabled || saving}
        onClick={async () => {
          setOpen(!open)
          if (!setting)
            try {
              apply(await api<Setting>(path))
              if (alive.current) setError("")
            } catch (e) {
              if (alive.current) setError(errorText(e))
            }
        }}>
        <Icon name="shield" size={17} />
      </button>
      {open && (
        <div className="permission-popover">
          <p className="permission-caption">Calendar events · This chat</p>
          <div role="menu" aria-label="Calendar approval mode">
            {(
              [
                [
                  "ask",
                  "Ask for approval",
                  "Review each event before it's created"
                ],
                [
                  "always",
                  "Always allow",
                  "Create requested events and send invitations"
                ]
              ] as const
            ).map(([mode, label, detail]) => (
              <button
                type="button"
                key={mode}
                role="menuitemradio"
                aria-checked={setting?.mode === mode}
                disabled={!setting || saving}
                onClick={() => void choose(mode)}>
                <Icon name="shield" size={17} />
                <span>
                  <strong>{label}</strong>
                  <small>{detail}</small>
                </span>
                <span aria-hidden="true" className="permission-check">
                  {setting?.mode === mode ? "✓" : ""}
                </span>
              </button>
            ))}
          </div>
          <p className="permission-caption">
            Email sending still needs approval. New chats ask again.
          </p>
          {error && (
            <p role="alert" className="permission-error">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
