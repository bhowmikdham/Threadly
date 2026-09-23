import type { Selection } from "../lib/types"
import { Icon } from "./Icon"

export function ContextPicker({
  selection,
  onChange,
  close,
  busy,
  rewrite
}: {
  selection: Selection
  onChange: (s: Selection | null) => void
  close: () => void
  busy: boolean
  rewrite: (messageId: string) => void
}) {
  return (
    <section className="context-picker" aria-label="Email context">
      <div className="card-heading">
        <b>In this conversation</b>
        <button
          className="icon-button"
          aria-label="Close context"
          onClick={close}>
          <Icon name="close" />
        </button>
      </div>
      <p className="context-title">{selection.thread.subject}</p>
      <p className="muted">
        Included messages keep their order. Choose a message when you mean a
        specific reply or passage.
      </p>
      {selection.messages.map((m, i) => (
        <div className="message-choice" key={m.gmail_msg_id}>
          <label className="check">
            <input
              type="checkbox"
              disabled={busy}
              checked={selection.selectedIds.includes(m.gmail_msg_id)}
              onChange={(e) =>
                onChange({
                  ...selection,
                  selectedIds: e.target.checked
                    ? [...selection.selectedIds, m.gmail_msg_id]
                    : selection.selectedIds.filter(
                        (id) => id !== m.gmail_msg_id
                      ),
                  targetId:
                    !e.target.checked && selection.targetId === m.gmail_msg_id
                      ? null
                      : selection.targetId
                })
              }
            />
            <span>
              {selection.selectedIds.includes(m.gmail_msg_id)
                ? `Message ${selection.messages.slice(0, i + 1).filter((x) => selection.selectedIds.includes(x.gmail_msg_id)).length}`
                : "Excluded"}{" "}
              · {m.from_addr}
              <small>
                {m.sent_at || m.received_at
                  ? new Date(m.sent_at || m.received_at).toLocaleString()
                  : ""}
              </small>
            </span>
          </label>
          <button
            disabled={busy}
            aria-pressed={selection.targetId === m.gmail_msg_id}
            onClick={() =>
              onChange({
                ...selection,
                targetId: m.gmail_msg_id,
                selectedIds: [
                  ...new Set([...selection.selectedIds, m.gmail_msg_id])
                ]
              })
            }>
            {selection.targetId === m.gmail_msg_id
              ? "Selected message"
              : "Use this message"}
          </button>
        </div>
      ))}
      {selection.targetId && (
        <button
          disabled={busy}
          onClick={() => {
            rewrite(selection.targetId)
            close()
          }}>
          Rewrite selected message
        </button>
      )}
      <button
        className="text-button"
        disabled={busy}
        onClick={() => {
          onChange(null)
          close()
        }}>
        Remove from conversation
      </button>
    </section>
  )
}
