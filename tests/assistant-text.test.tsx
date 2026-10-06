import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { AssistantText } from "../components/AssistantText"

describe("assistant prose", () => {
  it("renders escaped emphasis as literal punctuation without stripping arbitrary backslashes", () => {
    const { container } = render(
      <AssistantText
        text={
          "Use \\*\\*literal\\*\\* or **formatted**. Keep C:\\work and \\d."
        }
      />
    )
    expect(container.textContent).toBe(
      "Use **literal** or formatted. Keep C:\\work and \\d."
    )
    expect(
      [...container.querySelectorAll("strong")].map((node) => node.textContent)
    ).toEqual(["formatted"])
  })
  it("renders paragraphs, lists and emphasis instead of exposing Markdown markers", () => {
    const { container } = render(
      <AssistantText
        text={
          "Latest emails:\n\n1. **AliExpress** — Your order\n2. __Khan Academy__ — Review account\n\nChoose an email.\nI can read it with you."
        }
      />
    )
    expect(screen.getAllByRole("listitem")).toHaveLength(2)
    expect(container.querySelector("strong")?.textContent).toBe("AliExpress")
    expect(container.querySelectorAll("p")).toHaveLength(2)
    expect(container.textContent).not.toContain("**")
    expect(container.textContent).toContain(
      "Choose an email.\nI can read it with you."
    )
  })
  it("keeps untrusted HTML and links inert and incomplete markers intact", () => {
    const text =
      "<script>alert(1)</script>\n![tracking](https://example.test/open)\n[click](javascript:alert(1))\n\n`unfinished\n1. "
    const { container } = render(<AssistantText text={text} />)
    expect(container.querySelector("script,img,a")).toBeNull()
    expect(container.textContent).toContain("<script>alert(1)</script>")
    expect(container.textContent).toContain("`unfinished")
    expect(container.textContent).toContain("1. ")
  })
})
