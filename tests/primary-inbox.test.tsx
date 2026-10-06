import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { InboxCards } from "../components/InboxCards"
import { TaskCard } from "../components/TaskCard"
import { mailTimestamp } from "../lib/mail-display"
import type { Entry } from "../lib/types"
import { useAssistant } from "../lib/use-assistant"

function result(category?: "primary" | "all"): Entry {
  return {
    id: "primary-search",
    instruction: "Check my latest inbox email",
    inbox: {
      filters: {
        folder: "INBOX",
        ...(category ? { inbox_category: category } : {}),
        timezone: "Australia/Melbourne",
        received_from: "2026-10-06T13:00:00Z",
        received_before: "2026-10-06T13:30:00Z"
      },
      results: [
        {
          reference: "mail-1",
          message_id: "message-1",
          thread_id: "thread-1",
          subject: "Your receipt",
          sender: "Shop <receipt@example.test>",
          received_at: "2026-10-06T13:15:00Z",
          snippet: "₹1,999.00 — 20% off!!! Terms apply."
        }
      ],
      next_cursor: "next-page",
      coverage: { complete: false, page_size: 1 }
    }
  }
}

const controls = () => ({
  email: "me@example.test",
  chooseEmail: vi.fn(),
  moreEmails: vi.fn(),
  canPage: vi.fn().mockReturnValue(true)
})

describe("inbox category presentation", () => {
  it.each([
    ["primary", "Primary Inbox"],
    ["all", "All Inbox categories"],
    [undefined, "Inbox"]
  ] as const)(
    "honestly labels category %s without guessing a legacy scope",
    (category, label) => {
      const { container } = render(
        <InboxCards entry={result(category)} controller={controls()} />
      )
      expect(container.querySelector(".inbox-scope")?.textContent).toMatch(
        new RegExp(`^${label} · Times in Australia/Melbourne`)
      )
    }
  )

  it.each([
    ["all_mail", "All mail"],
    ["SENT", "Sent"]
  ])("does not apply a Primary label to folder %s", (folder, label) => {
    const value = result("primary")
    value.inbox!.filters.folder = folder
    const { container } = render(
      <InboxCards entry={value} controller={controls()} />
    )
    expect(container.querySelector(".inbox-scope")?.textContent).toContain(
      `${label} · Times in Australia/Melbourne`
    )
    expect(screen.queryByText(/Primary Inbox/)).toBeNull()
  })

  it("retains the typed category, record identity and source selection when paging", () => {
    const value = result("primary")
    const before = JSON.stringify(value)
    const controller = controls()
    const { rerender } = render(
      <InboxCards entry={value} controller={controller} />
    )
    expect(screen.getByText("₹1,999.00 — 20% off!!! Terms apply.")).toBeTruthy()
    fireEvent.click(
      screen.getByRole("button", { name: "Use email: Your receipt" })
    )
    expect(controller.chooseEmail.mock.calls[0][0]).toBe(
      value.inbox!.results[0]
    )
    fireEvent.click(screen.getByRole("button", { name: "Show more emails" }))
    expect(controller.moreEmails.mock.calls[0][0]).toBe(value)
    expect(
      controller.moreEmails.mock.calls[0][0].inbox.filters.inbox_category
    ).toBe("primary")
    rerender(
      <InboxCards
        entry={value}
        controller={{ ...controller, selection: { targetId: "message-1" } }}
      />
    )
    expect(
      screen
        .getByRole("button", { name: "Use email: Your receipt" })
        .getAttribute("aria-pressed")
    ).toBe("true")
    expect(JSON.stringify(value)).toBe(before)
  })

  it("shows the recipient's next local day across local midnight and keeps the original timestamp", () => {
    const value = result("primary")
    const { container } = render(
      <InboxCards entry={value} controller={controls()} />
    )
    const timestamp = value.inbox!.results[0].received_at
    expect(mailTimestamp(timestamp, "Australia/Melbourne", "en-AU")).toMatch(
      /7 Oct 2026.*12:15 am.*AEDT/i
    )
    expect(container.querySelector("time")?.getAttribute("datetime")).toBe(
      timestamp
    )
    expect(container.querySelector("time")?.textContent).toBe(
      mailTimestamp(timestamp, "Australia/Melbourne")
    )
    expect(
      container.querySelector(".inbox-date-window")?.textContent
    ).toContain(mailTimestamp("2026-10-06T13:30:00Z", "Australia/Melbourne"))
  })

  it("distinguishes the repeated local hour when Melbourne daylight saving ends", () => {
    expect(
      mailTimestamp("2026-04-04T15:59:00Z", "Australia/Melbourne", "en-AU")
    ).toMatch(/5 Apr 2026.*2:59 am.*AEDT/i)
    expect(
      mailTimestamp("2026-04-04T16:01:00Z", "Australia/Melbourne", "en-AU")
    ).toMatch(/5 Apr 2026.*2:01 am.*AEST/i)
  })
})

describe("empty source evidence", () => {
  const invisible = [
    "",
    " \n\t ",
    "&nbsp;",
    "&amp;zwnj;&amp;zwnj;",
    "&amp;zwnj;",
    "\u200b\ufeff\u2060",
    "\u200c",
    "\u200d",
    "\u200e\u200f"
  ]

  it.each(invisible)(
    "omits a source quote that becomes invisible: %j",
    (quote) => {
      const value = result("primary")
      value.evidence = [{ reference: "mail-1", quote }]
      const original = JSON.stringify(value)
      const { container } = render(
        <TaskCard entry={value} controller={controls()} />
      )
      expect(screen.queryByText("Sources", { exact: true })).toBeNull()
      expect(container.querySelector("blockquote")).toBeNull()
      expect(JSON.stringify(value)).toBe(original)
    }
  )

  it("omits blank and duplicate excerpts while preserving meaningful distinct evidence verbatim", () => {
    const value = result("primary")
    const meaningful =
      "Amount: ₹1,999.00!!! Delivery tomorrow. می\u200cخواهم 👩\u200d💻"
    value.evidence = [
      ...invisible.map((quote) => ({ reference: "mail-1", quote })),
      { reference: "mail-1", quote: value.inbox!.results[0].snippet },
      { reference: "mail-1", quote: meaningful }
    ]
    const original = JSON.stringify(value)
    const { container } = render(
      <TaskCard entry={value} controller={controls()} />
    )
    expect(screen.getByText("Sources", { exact: true })).toBeTruthy()
    expect(container.querySelectorAll("blockquote")).toHaveLength(1)
    expect(container.querySelector("blockquote")?.textContent).toBe(meaningful)
    expect(JSON.stringify(value)).toBe(original)
  })

  it.each([
    ["Please review your account now.", 31],
    ["Your account update is now ready to review.", 43]
  ] as const)(
    "renders a meaningful backend quote through the conversation controller: %s",
    async (quote, length) => {
      // Synthetic text with the production-observed lengths, not real mail.
      // The transport uses {reference, quote}; no snippet/excerpt alias is needed.
      expect(quote).toHaveLength(length)
      const previousStorage = chrome.storage.session
      ;(chrome.storage as any).session = {
        get: vi.fn().mockResolvedValue({}),
        set: vi.fn().mockResolvedValue(undefined),
        remove: vi.fn()
      }
      vi.mocked(chrome.runtime.sendMessage).mockImplementation((async (
        message: any
      ) => ({
        ok: true,
        data: {
          conversation_id: message.body.conversation_id,
          version: message.body.expected_version + 1,
          kind: "message",
          text: "Here is the latest Primary Inbox email.",
          search: result("primary").inbox,
          evidence: [{ reference: "mail-1", quote }]
        }
      })) as any)
      function Conversation() {
        const controller = useAssistant({
          id: 1,
          email: "me@example.test",
          name: "Tester"
        })
        return (
          <>
            <button
              disabled={controller.restoring}
              onClick={() =>
                void controller.submit("Check my latest inbox email")
              }>
              Check inbox
            </button>
            {controller.entries.map((entry) => (
              <TaskCard key={entry.id} entry={entry} controller={controller} />
            ))}
          </>
        )
      }
      try {
        const { container } = render(<Conversation />)
        const submit = screen.getByRole("button", {
          name: "Check inbox"
        }) as HTMLButtonElement
        await waitFor(() => expect(submit.disabled).toBe(false))
        fireEvent.click(submit)
        await screen.findByText(quote)
        const summary = screen.getByText("Sources", { exact: true })
        const disclosure = summary.closest("details")!
        expect(disclosure.open).toBe(false)
        fireEvent.click(summary)
        expect(disclosure.open).toBe(true)
        expect(container.querySelector("blockquote")?.textContent).toBe(quote)
        expect(screen.getByText(/Primary Inbox · Times in/)).toBeTruthy()
      } finally {
        ;(chrome.storage as any).session = previousStorage
      }
    }
  )
})
