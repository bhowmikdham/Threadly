import { useState } from "react"

import { api, errorText } from "../lib/api"

export function MailSearch({
  select
}: {
  select: (id: string) => Promise<void>
}) {
  const [query, setQuery] = useState(""),
    [days, setDays] = useState(7),
    [folder, setFolder] = useState("all_mail"),
    [result, setResult] = useState<any>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [lastBody, setLastBody] = useState<any>(null)
  const search = async (more = false) => {
    setBusy(true)
    setError("")
    try {
      const now = new Date(),
        start = new Date(now.getTime() - days * 86400000)
      const body = more
        ? { ...lastBody, cursor: result.next_cursor }
        : {
            schema_version: "1.0",
            query: query.trim(),
            folder,
            received_from: start.toISOString(),
            received_before: now.toISOString()
          }
      const r = await api("/assistant/mail-search", body)
      setLastBody(body)
      setResult(
        more
          ? { ...r, results: [...(result.results || []), ...(r.results || [])] }
          : r
      )
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="artifact">
      <h2>Search mail</h2>
      <p className="muted">
        Fetch a bounded date window from Gmail. No mailbox import.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void search()
        }}>
        <label>
          Search text
          <input
            required
            maxLength={200}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <div className="button-row">
          <label>
            Past days
            <input
              type="number"
              min={1}
              max={30}
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
            />
          </label>
          <label>
            Folder
            <select value={folder} onChange={(e) => setFolder(e.target.value)}>
              <option value="all_mail">All mail</option>
              <option value="INBOX">Inbox</option>
              <option value="SENT">Sent</option>
            </select>
          </label>
        </div>
        <button disabled={busy || !query.trim()}>Search</button>
      </form>
      {result && (
        <>
          <p>{result.results?.length || 0} results on loaded pages.</p>
          {result.results?.map((r: any, i: number) => (
            <button
              className="history-row"
              key={r.gmail_msg_id || i}
              disabled={busy}
              onClick={() => void select(r.gmail_thread_id || r.thread_id)}>
              <span>{r.subject || "No subject"}</span>
              <small>{r.from_addr || r.sender || ""}</small>
            </button>
          ))}
          {result.next_cursor && (
            <button disabled={busy} onClick={() => void search(true)}>
              Load next page
            </button>
          )}
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  )
}
