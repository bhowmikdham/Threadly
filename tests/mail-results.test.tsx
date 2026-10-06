import { fireEvent, render, screen, within } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { InboxCards } from "../components/InboxCards"
import { TaskCard } from "../components/TaskCard"
import {
  displayTimezone,
  mailDateBound,
  mailExcerpt,
  mailText,
  mailTimestamp
} from "../lib/mail-display"
import type { Entry, InboxResult } from "../lib/types"

const mail: InboxResult = {
  reference: "mail-1",
  message_id: "message-1",
  thread_id: "thread-1",
  sender: "AliExpress <offers@example.test>",
  subject: "To pair with what you purchased",
  received_at: "2026-10-06T11:26:00Z",
  snippet:
    "Your order is ready. &amp;zwnj;&amp;zwnj;\u200b\u200c\u200c See matching items. https://example.test/click?id=tracking\nUnsubscribe from these emails."
}
const entry = (results = [mail]): Entry => ({
  id: "search-1",
  instruction: "Find my latest inbox mail",
  inbox: {
    filters: {
      folder: "INBOX",
      timezone: "Australia/Melbourne",
      received_from: "2026-10-05",
      received_before: "2026-10-07"
    },
    results,
    next_cursor: null,
    coverage: { complete: true, page_size: 5 }
  }
})
const controller = () => ({
  email: "me@example.test",
  chooseEmail: vi.fn(),
  canPage: vi.fn().mockReturnValue(true),
  moreEmails: vi.fn(),
  selection: null
})

describe("display-only mail text", () => {
  it("cleans nested entities, invisible padding, tracking URLs and footer clutter", () => {
    expect(mailExcerpt(mail.snippet)).toBe(
      "Your order is ready. See matching items."
    )
    expect(mailText("Tom &amp; Jerry &#x1f600; &unknown;")).toBe(
      "Tom & Jerry 😀 &unknown;"
    )
    expect(
      mailExcerpt("Read [the offer](https://example.test/click?id=1) now.")
    ).toBe("Read the offer now.")
  })
  it("preserves meaningful joiners, ordinary links and inline unsubscribe discussion", () => {
    const text =
      "می\u200cخواهم 👩\u200d💻 Read https://example.test/guide to unsubscribe safely."
    expect(mailExcerpt(text)).toBe(text)
    expect(mailExcerpt("Please manage your preferences before replying.")).toBe(
      "Please manage your preferences before replying."
    )
  })
  it("bounds long preview text and treats decoded markup as text", () => {
    expect(mailExcerpt("x".repeat(10000))).toHaveLength(240)
    expect(mailText("&lt;img src=x onerror=alert(1)&gt;")).toBe(
      "<img src=x onerror=alert(1)>"
    )
  })
})

describe("received instants and date windows", () => {
  it("converts authoritative UTC instants to Melbourne, including date rollover", () => {
    expect(
      mailTimestamp(mail.received_at, "Australia/Melbourne", "en-AU")
    ).toMatch(/6 Oct 2026.*10:26 pm.*AEDT/i)
    expect(
      mailTimestamp("2026-10-06T23:30:00Z", "Australia/Melbourne", "en-AU")
    ).toMatch(/7 Oct 2026.*10:30 am.*AEDT/i)
  })
  it("uses the DST offset at the email's instant across the spring transition", () => {
    expect(
      mailTimestamp("2026-10-03T15:59:00Z", "Australia/Melbourne", "en-AU")
    ).toMatch(/4 Oct 2026.*1:59 am.*AEST/i)
    expect(
      mailTimestamp("2026-10-03T16:01:00Z", "Australia/Melbourne", "en-AU")
    ).toMatch(/4 Oct 2026.*3:01 am.*AEDT/i)
  })
  it("preserves literal date bounds and refuses to invent an instant", () => {
    expect(mailDateBound("2026-10-06", "America/Los_Angeles")).toMatch(/6/)
    expect(
      mailTimestamp("2026-10-06T11:26:00", "Australia/Melbourne")
    ).toBeNull()
    expect(mailTimestamp("invalid", "Australia/Melbourne")).toBeNull()
    expect(displayTimezone("Invalid/Zone")).toBe(displayTimezone())
  })
  it("keeps the time on an exclusive timestamp bound within the current local day", () => {
    expect(mailDateBound("2026-10-06T12:20:00Z", "Australia/Melbourne")).toBe(
      mailTimestamp("2026-10-06T12:20:00Z", "Australia/Melbourne")
    )
    expect(
      mailDateBound("2026-10-06T12:20:00Z", "Australia/Melbourne")
    ).toMatch(/11:20|23:20/)
  })
})

describe("mail results and provenance", () => {
  it("shows the local received time and scope, while selecting the untouched original record", () => {
    const value = entry()
    const original = JSON.stringify(value)
    const control = controller()
    const { container } = render(
      <InboxCards entry={value} controller={control} />
    )
    expect(
      screen.getByText(/Inbox · Times in Australia\/Melbourne/)
    ).toBeTruthy()
    expect(screen.getByText(/From .*before/)).toBeTruthy()
    const time = container.querySelector("time")!
    expect(time.getAttribute("datetime")).toBe(mail.received_at)
    expect(time.textContent).toBe(
      mailTimestamp(mail.received_at, "Australia/Melbourne")
    )
    expect(time.title).toContain(`Original timestamp: ${mail.received_at}`)
    expect(
      screen.getByText("Your order is ready. See matching items.")
    ).toBeTruthy()
    fireEvent.click(
      screen.getByRole("button", { name: `Use email: ${mail.subject}` })
    )
    expect(control.chooseEmail).toHaveBeenCalledWith(mail)
    expect(control.chooseEmail.mock.calls[0][0]).toBe(mail)
    expect(JSON.stringify(value)).toBe(original)
  })
  it("never interprets email markup as HTML and retains the selected state", () => {
    const unsafe = {
      ...mail,
      subject: "&lt;script&gt;alert(1)&lt;/script&gt;",
      snippet: "&lt;img src=x onerror=alert(1)&gt;"
    }
    const { container } = render(
      <InboxCards
        entry={entry([unsafe])}
        controller={{
          ...controller(),
          selection: { targetId: mail.message_id }
        }}
      />
    )
    expect(container.querySelector("script,img")).toBeNull()
    expect(
      screen
        .getByRole("button", { name: "Use email: <script>alert(1)</script>" })
        .getAttribute("aria-pressed")
    ).toBe("true")
    expect(screen.getByText("In this conversation")).toBeTruthy()
  })
  it.each(["busy", "restoring", "restoreFailed", "contextLocked"])(
    "keeps selection disabled while %s",
    (flag) => {
      const control = { ...controller(), [flag]: true }
      render(<InboxCards entry={entry()} controller={control} />)
      const button = screen.getByRole("button", {
        name: `Use email: ${mail.subject}`
      }) as HTMLButtonElement
      expect(button.disabled).toBe(true)
      fireEvent.click(button)
      expect(control.chooseEmail).not.toHaveBeenCalled()
    }
  )
  it("reveals retained cards before paging and preserves result order and cursor ownership", () => {
    const mails = Array.from({ length: 7 }, (_, i) => ({
      ...mail,
      message_id: `message-${i}`,
      subject: `Result ${i}`
    }))
    const value = entry(mails)
    value.inbox!.next_cursor = "next-page"
    const control = controller()
    const { container, rerender } = render(
      <InboxCards entry={value} controller={control} />
    )
    expect(container.querySelectorAll(".mail-glass-card")).toHaveLength(5)
    expect(
      screen.queryByRole("button", { name: "Show more emails" })
    ).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Show 2 more results" }))
    expect(
      [...container.querySelectorAll(".mail-card-content strong")].map(
        (node) => node.textContent
      )
    ).toEqual(mails.map((item) => item.subject))
    expect(control.moreEmails).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("button", { name: "Show more emails" }))
    expect(control.moreEmails).toHaveBeenCalledWith(value)
    control.canPage.mockReturnValue(false)
    rerender(<InboxCards entry={value} controller={control} />)
    expect(
      screen.queryByRole("button", { name: "Show more emails" })
    ).toBeNull()
  })
  it("handles empty, malformed and missing-time results without inventing a date", () => {
    const { rerender } = render(
      <InboxCards entry={entry([])} controller={controller()} />
    )
    expect(screen.getByText("No matching emails.")).toBeTruthy()
    rerender(
      <InboxCards
        entry={entry([{ ...mail, received_at: "bad" }])}
        controller={controller()}
      />
    )
    expect(screen.getByText("Received time unavailable")).toBeTruthy()
    rerender(
      <InboxCards
        entry={{ id: "bad", instruction: "", inbox: {} as any }}
        controller={controller()}
      />
    )
    expect(screen.getByRole("alert").textContent).toContain(
      "could not be displayed"
    )
  })
  it("omits repeated Sources excerpts while retaining distinct factual evidence", () => {
    const value = entry([{ ...mail, snippet: "Your order is ready." }])
    value.message = "Your newest email is from **AliExpress**."
    value.evidence = [{ reference: "mail-1", quote: "Your order is ready." }]
    const { rerender, container } = render(
      <TaskCard entry={value} controller={controller()} />
    )
    expect(screen.queryByText("Sources")).toBeNull()
    expect(container.querySelector(".chat-response strong")?.textContent).toBe(
      "AliExpress"
    )
    value.evidence = [
      ...value.evidence,
      { reference: "mail-1", quote: "Delivery is scheduled for Friday." },
      { reference: "mail-previous", quote: "A separate earlier source." }
    ]
    rerender(<TaskCard entry={value} controller={controller()} />)
    const sources = screen.getByText("Sources").closest("details")!
    expect(within(sources).queryByText("Your order is ready.")).toBeNull()
    expect(
      within(sources).getByText("Delivery is scheduled for Friday.")
    ).toBeTruthy()
    expect(within(sources).getByText("A separate earlier source.")).toBeTruthy()
    expect(value.evidence).toHaveLength(3)
  })
  it("retains evidence for a card outside the initially visible batch and preserves source quotes", () => {
    const value = entry(
      Array.from({ length: 6 }, (_, i) => ({
        ...mail,
        reference: `mail-${i + 1}`,
        message_id: `message-${i + 1}`,
        snippet: "Quoted source text."
      }))
    )
    value.evidence = [
      { reference: "mail-6", quote: "Quoted source text." },
      {
        reference: "mail-1",
        quote:
          "Your cancellation is recorded. Unsubscribe from reminders if needed. https://example.test/click?receipt=1"
      }
    ]
    render(<TaskCard entry={value} controller={controller()} />)
    const sources = screen.getByText("Sources").closest("details")!
    expect(within(sources).getByText("Quoted source text.")).toBeTruthy()
    expect(within(sources).getByText(value.evidence[1].quote)).toBeTruthy()
  })
})
