import { useState } from "react"

import type { Entry, InboxResult } from "../lib/types"
import { Icon } from "./Icon"

const VISIBLE_MAIL_BATCH = 5

export function FlightCard({
  flight
}: {
  flight: NonNullable<InboxResult["flight"]>
}) {
  return (
    <div
      className="flight-ticket"
      aria-label={`Flight from ${flight.origin} to ${flight.destination}`}>
      <div className="flight-caption">
        <span>FLIGHT ITINERARY</span>
        {flight.flight_number && <b>{flight.flight_number}</b>}
      </div>
      <div className="flight-route">
        <b>{flight.origin}</b>
        <span>TO</span>
        <b>{flight.destination}</b>
      </div>
      <svg className="flight-map" viewBox="0 0 280 94" aria-hidden="true">
        <path className="flight-arc" d="M20 72 Q140 -8 260 72" />
        <circle cx="20" cy="72" r="4" />
        <circle cx="260" cy="72" r="4" />
        <g className="flight-plane">
          <path d="M-10 -3 -2 -2 1 -10 4 -10 4 -2 11 0 4 2 4 10 1 10 -2 2 -10 3 -7 0Z" />
        </g>
        <g className="flight-plane-static" transform="translate(140 32)">
          <path d="M-10 -3 -2 -2 1 -10 4 -10 4 -2 11 0 4 2 4 10 1 10 -2 2 -10 3 -7 0Z" />
        </g>
      </svg>
      <small title={flight.route_quote}>
        From your email · not live flight status
      </small>
    </div>
  )
}
export function InboxCards({
  entry,
  controller
}: {
  entry: Entry
  controller: any
}) {
  const [visibleCount, setVisibleCount] = useState(VISIBLE_MAIL_BATCH)
  const page = entry.inbox
  if (
    !page ||
    !page.filters ||
    !Array.isArray(page.results) ||
    page.results.some(
      (mail) =>
        !mail ||
        typeof mail.message_id !== "string" ||
        typeof mail.thread_id !== "string"
    )
  )
    return (
      <p className="warning" role="alert">
        These email results could not be displayed. Please run the search again.
      </p>
    )
  const dates = [page.filters.received_from, page.filters.received_before]
    .filter((x) => typeof x === "string" && !Number.isNaN(Date.parse(x)))
    .map((x) =>
      new Date(x).toLocaleDateString(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric"
      })
    )
  return (
    <section className="inbox-results" aria-label="Email results">
      <p className="inbox-intro">
        {page.results.length
          ? page.results.length > visibleCount
            ? `Showing ${visibleCount} of ${page.results.length} emails from this search.`
            : `Here ${page.results.length === 1 ? "is" : "are"} ${page.results.length} ${page.results.length === 1 ? "email" : "emails"} from this search.`
          : "I didn’t find any matching emails in these dates."}
      </p>
      {dates.length > 0 && (
        <p className="inbox-scope">
          {dates.join(" – ")}
          {page.filters.folder === "INBOX"
            ? " · Inbox"
            : page.filters.folder === "SENT"
              ? " · Sent"
              : ""}
        </p>
      )}
      <div className="mail-card-list">
        {page.results.slice(0, visibleCount).map((mail, i) => {
          const selected = controller.selection?.targetId === mail.message_id
          return (
            <article
              className={`mail-glass-card${selected ? " is-selected" : ""}`}
              key={mail.message_id}
              style={{ "--card-order": Math.min(i, 4) } as React.CSSProperties}>
              <button
                className="mail-card-main"
                disabled={
                  controller.busy ||
                  controller.restoring ||
                  controller.restoreFailed ||
                  controller.contextLocked
                }
                aria-label={`Use email: ${mail.subject || "No subject"}`}
                aria-pressed={selected}
                onClick={() => void controller.chooseEmail(mail)}>
                <span className="mail-card-icon">
                  <Icon name="mail" size={19} />
                </span>
                <span className="mail-card-content">
                  <span className="mail-card-meta">
                    <span>{mail.sender || "Email"}</span>
                    <time dateTime={mail.received_at}>
                      {new Date(mail.received_at).toLocaleDateString(
                        undefined,
                        { month: "short", day: "numeric" }
                      )}
                    </time>
                  </span>
                  <strong>{mail.subject || "No subject"}</strong>
                  <span className="mail-card-snippet">
                    {mail.snippet || "Open this email to read it."}
                  </span>
                </span>
              </button>
              {mail.flight && <FlightCard flight={mail.flight} />}
              <div className="mail-card-actions">
                <small className="mail-card-state">
                  {selected && <Icon name="check" size={12} />}
                  {selected
                    ? "In this conversation"
                    : "Select for this conversation"}
                </small>
                <button
                  className="icon-button"
                  title="Open in Gmail"
                  aria-label={`Open in Gmail: ${mail.subject}`}
                  onClick={() =>
                    void chrome.tabs.create({
                      url: `https://mail.google.com/mail/?authuser=${encodeURIComponent(controller.email)}#all/${encodeURIComponent(mail.thread_id)}`
                    })
                  }>
                  <Icon name="external" size={14} />
                </button>
              </div>
            </article>
          )
        })}
      </div>
      {page.results.length > visibleCount && (
        <button
          className="show-more-mail"
          onClick={() =>
            setVisibleCount((count) => count + VISIBLE_MAIL_BATCH)
          }>
          Show{" "}
          {Math.min(VISIBLE_MAIL_BATCH, page.results.length - visibleCount)}{" "}
          more results
          <Icon name="chevron" size={14} />
        </button>
      )}
      {page.results.length <= visibleCount &&
        page.next_cursor &&
        controller.canPage(entry) && (
          <button
            className="show-more-mail"
            disabled={controller.busy}
            onClick={() => void controller.moreEmails(entry)}>
            Show more emails <Icon name="chevron" size={14} />
          </button>
        )}
      {!page.results.length && (
        <p className="muted">Try another sender, subject or date range.</p>
      )}
    </section>
  )
}
