import { useEffect, useState } from "react"

import { categoryIcon, priorityIcon, replyIcon } from "../lib/classification"
import {
  INBOX_MARKS_KEY,
  inboxMarks,
  type InboxMarks
} from "../lib/inbox-marks"

import "./InboxMarksSettings.css"

export function InboxMarksSettings() {
  const [value, setValue] = useState<InboxMarks | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  useEffect(() => {
    let active = true
    void chrome.storage.local
      .get(INBOX_MARKS_KEY)
      .then((saved) => {
        if (active) setValue(inboxMarks(saved[INBOX_MARKS_KEY]))
      })
      .catch(() => {
        if (active)
          setError(
            "Inbox settings could not be loaded. Reopen Settings to try again."
          )
      })
    const changed = (
      changes: Record<string, chrome.storage.StorageChange>,
      area: string
    ) => {
      if (area === "local" && changes[INBOX_MARKS_KEY])
        setValue(inboxMarks(changes[INBOX_MARKS_KEY].newValue))
    }
    chrome.storage.onChanged.addListener(changed)
    return () => {
      active = false
      chrome.storage.onChanged.removeListener(changed)
    }
  }, [])
  const save = async (next: InboxMarks) => {
    const previous = value
    setValue(next)
    setBusy(true)
    setError("")
    try {
      await chrome.storage.local.set({ [INBOX_MARKS_KEY]: next })
      setValue(next)
    } catch {
      setValue(previous)
      setError("Inbox settings could not be saved. Try again.")
    } finally {
      setBusy(false)
    }
  }
  return (
    <section
      className="settings-section inbox-marks-settings"
      aria-labelledby="inbox-marks-heading">
      <h3 id="inbox-marks-heading">A more readable inbox</h3>
      <p className="muted">
        Priority, reply and category marks help you scan Gmail. They leave every
        message in place.
      </p>
      <div
        className="inbox-marks-preview"
        role="img"
        aria-label="Example: High priority, reply needed, Legal">
        <span className="inbox-marks-example">EXAMPLE</span>
        <div className="inbox-marks-sample" aria-hidden="true">
          <strong>Hartley &amp; Vance LLP</strong>
          <span className="inbox-marks-sample-left">
            <span
              className="inbox-marks-high"
              dangerouslySetInnerHTML={{ __html: priorityIcon.High }}
            />
            <span dangerouslySetInnerHTML={{ __html: replyIcon }} />
            <span className={value?.words ? "" : "inbox-marks-hidden"}>
              Reply
            </span>
          </span>
          <span className="inbox-marks-sample-subject">
            Agreement: redlines for your review
          </span>
          <span className="inbox-marks-sample-category">
            <span
              dangerouslySetInnerHTML={{ __html: categoryIcon.legal_contracts }}
            />
            <span className={value?.words ? "" : "inbox-marks-hidden"}>
              Legal
            </span>
          </span>
        </div>
        <p className="muted">
          High · Medium · Low priority
          <br />
          Reply needed or no reply needed · Category
        </p>
      </div>
      <label className="inbox-marks-option">
        <input
          type="checkbox"
          checked={value?.enabled ?? true}
          disabled={!value || busy}
          onChange={(e) => void save({ ...value, enabled: e.target.checked })}
        />
        <span>Show marks in Gmail</span>
      </label>
      <label className="inbox-marks-option">
        <input
          type="checkbox"
          checked={value?.words ?? false}
          disabled={!value || busy}
          onChange={(e) => void save({ ...value, words: e.target.checked })}
        />
        <span>Rather have words than icons</span>
      </label>
      <p className="muted">
        Applies to this browser. Marks use Threadly’s existing read access for
        visible inbox threads. Hover, focus or tap a mark for its meaning.
        Uncertain results stay blank. You can hide marks at any time.
      </p>
      {error && <p role="alert">{error}</p>}
    </section>
  )
}
