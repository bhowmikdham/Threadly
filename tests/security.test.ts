import { describe, expect, it } from "vitest"

import { addresses } from "../lib/api"
import { gmailId, readGmailSelection } from "../lib/gmail-context"
import {
  allowedPath,
  backendOrigin,
  callbackCode,
  trustedSender
} from "../lib/security"

describe("extension trust boundaries", () => {
  it.each([
    "http://example.com",
    "https://name:secret@example.com",
    "https://example.com/path",
    "https://example.com/#jwt",
    "https://example.com/?token=x"
  ])("rejects unsafe backend %s", (v) =>
    expect(() => backendOrigin(v)).toThrow()
  )
  it.each([
    "https://api.example.com",
    "http://127.0.0.1:8000",
    "http://localhost:8000"
  ])("allows an explicit server origin %s", (v) =>
    expect(backendOrigin(v)).toBe(v)
  )
  it.each([
    "https://evil.example/assistant",
    "//evil.example",
    "/assistant/../auth",
    "/assistant/%2f/evil",
    "/assistant/x#fragment",
    "/sync/jobs"
  ])("blocks unsafe bridge paths %s", (v) => expect(allowedPath(v)).toBe(false))
  it("does not grant Gmail content scripts privileged backend access", () => {
    expect(
      trustedSender(
        { id: "abc", url: "https://mail.google.com/", tab: {} },
        "abc"
      )
    ).toBe(false)
    expect(
      trustedSender(
        { id: "abc", url: "chrome-extension://abc/sidepanel.html", tab: {} },
        "abc"
      )
    ).toBe(true)
    expect(
      trustedSender(
        { id: "other", url: "chrome-extension://abc/sidepanel.html" },
        "abc"
      )
    ).toBe(false)
  })
  it.each([
    "https://evil.example/oauth/callback?state=s&code=c",
    "https://abc.chromiumapp.org/wrong?state=s&code=c",
    "https://abc.chromiumapp.org/oauth/callback?state=other&code=c",
    "https://abc.chromiumapp.org/oauth/callback?state=s&state=s&code=c",
    "https://abc.chromiumapp.org/oauth/callback?state=s&code=c&code=d",
    "https://abc.chromiumapp.org/oauth/callback?state=s&error=denied"
  ])("rejects mismatched OAuth response %s", (v) =>
    expect(() =>
      callbackCode(v, "https://abc.chromiumapp.org/oauth/callback", "s")
    ).toThrow()
  )
  it("validates a bound callback", () =>
    expect(
      callbackCode(
        "https://abc.chromiumapp.org/oauth/callback?state=s&code=c",
        "https://abc.chromiumapp.org/oauth/callback",
        "s"
      )
    ).toBe("c"))
  it("does not silently drop recipients", () => {
    expect(addresses("one@example.test; two@example.test")).toEqual([
      "one@example.test",
      "two@example.test"
    ])
    expect(() => addresses("Name <one@example.test>")).toThrow()
    expect(() => addresses("one@example.test,ONE@example.test")).toThrow()
  })
})
describe("selected Gmail references", () => {
  it("accepts provider hex IDs without inventing the 16-character constraint", () => {
    expect(gmailId("abc123")).toBe("abc123")
    expect(gmailId("#msg-f:123456")).toBeNull()
  })
  it("does not select an inbox row as an open thread", () => {
    document.body.innerHTML =
      '<main role="main"><div data-legacy-thread-id="abcdef"></div></main>'
    expect(
      readGmailSelection(document, "https://mail.google.com/#inbox").threadId
    ).toBeNull()
  })
  it("keeps real IDs and never emits message bodies or fabricated dates", () => {
    document.body.innerHTML =
      '<main role="main"><h2 class="hP">Receipt</h2><div data-legacy-thread-id="abcdef"><div data-legacy-message-id="abcd1" aria-expanded="true"><div class="a3s">PRIVATE_BODY</div></div><div data-legacy-message-id="abcd2"></div></div></main>'
    const s = readGmailSelection(
      document,
      "https://mail.google.com/#inbox/abcdef"
    )
    expect(s.threadId).toBe("abcdef")
    expect(s.messageIds).toEqual(["abcd1", "abcd2"])
    expect(s.selectedMessageId).toBe("abcd1")
    expect(JSON.stringify(s)).not.toContain("PRIVATE_BODY")
  })
  it("requires explicit target when multiple messages are expanded", () => {
    document.body.innerHTML =
      '<h2 class="hP">Receipt</h2><div data-legacy-message-id="a1" aria-expanded="true"></div><div data-legacy-message-id="a2" aria-expanded="true"></div>'
    expect(
      readGmailSelection(document, "https://mail.google.com/#inbox/ab")
        .selectedMessageId
    ).toBeNull()
  })
})
