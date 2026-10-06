import { Fragment, type ReactNode } from "react"

// A deliberately small text-only Markdown subset for assistant prose. HTML,
// images and link destinations are never interpreted or injected into the DOM.
function inline(text: string): ReactNode[] {
  const tokens = text.split(
    /(\\[\\*_`]|\*\*[^*\n]+\*\*|__[^_\n]+__|`[^`\n]+`|\*[^*\n]+\*)/g
  )
  return tokens.map((token, index) => {
    if (/^\\[\\*_`]$/.test(token))
      return <Fragment key={index}>{token[1]}</Fragment>
    if (/^(?:\*\*[^*\n]+\*\*|__[^_\n]+__)$/.test(token))
      return <strong key={index}>{token.slice(2, -2)}</strong>
    if (/^`[^`\n]+`$/.test(token))
      return <code key={index}>{token.slice(1, -1)}</code>
    if (/^\*[^*\n]+\*$/.test(token))
      return <em key={index}>{token.slice(1, -1)}</em>
    return <Fragment key={index}>{token}</Fragment>
  })
}

export function AssistantText({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  const lines = text.replace(/\r\n?/g, "\n").split("\n")
  for (let i = 0; i < lines.length; ) {
    if (!lines[i].trim()) {
      i++
      continue
    }
    const list = lines[i].match(/^\s*(?:(\d+)[.)]|([-*]))\s+(.+)$/)
    if (list) {
      const ordered = Boolean(list[1])
      const items: ReactNode[] = []
      while (i < lines.length) {
        const item = lines[i].match(/^\s*(?:(\d+)[.)]|([-*]))\s+(.+)$/)
        if (!item || Boolean(item[1]) !== ordered) break
        items.push(<li key={i}>{inline(item[3])}</li>)
        i++
      }
      blocks.push(
        ordered ? (
          <ol key={i} start={Number(list[1])}>
            {items}
          </ol>
        ) : (
          <ul key={i}>{items}</ul>
        )
      )
    } else {
      const paragraph: string[] = []
      while (
        i < lines.length &&
        lines[i].trim() &&
        !/^\s*(?:\d+[.)]|[-*])\s+.+$/.test(lines[i])
      ) {
        paragraph.push(lines[i++])
      }
      blocks.push(<p key={i}>{inline(paragraph.join("\n"))}</p>)
    }
  }
  return <div className="chat-response">{blocks}</div>
}
