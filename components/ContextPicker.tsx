import type { Selection } from "../lib/types"
import { Icon } from "./Icon"

// What this conversation is about: the attached email's subject and who wrote
// it. Read-only; detaching lives on the chip's own remove button.
export function ContextPicker({
  selection,
  close
}: {
  selection: Selection
  close: () => void
}) {
  const messages = selection.messages.filter((m) =>
    selection.selectedIds.includes(m.gmail_msg_id)
  )
  return (
    <section className="context-picker" aria-label="Email context">
      <div className="card-heading">
        <b>In this conversation</b>
        <button
          className="icon-button"
          aria-label="Close email details"
          onClick={close}>
          <Icon name="close" />
        </button>
      </div>
      <p className="context-title">{selection.thread.subject}</p>
      {messages.map((m) => (
        <p className="context-from" key={m.gmail_msg_id}>
          <Icon name="mail" size={14} />
          <span>{m.from_addr}</span>
          {(m.sent_at || m.received_at) && (
            <small>
              {new Date(m.sent_at || m.received_at).toLocaleDateString(
                undefined,
                { day: "numeric", month: "short" }
              )}
            </small>
          )}
        </p>
      ))}
    </section>
  )
}
