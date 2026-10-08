import { Logo } from "../components/Icon"

import "../style.css"

export default function Updated() {
  const version = chrome.runtime.getManifest().version
  return (
    <main className="threadly update-page">
      <article className="update-card">
        <Logo height={40} />
        <p className="update-eyebrow">WHAT’S NEW · VERSION {version}</p>
        <h1>Threadly was updated</h1>
        <p className="update-intro">Pick up where you left off.</p>
        <section>
          <h2>Return to earlier work</h2>
          <p>
            Keep drafts and event requests in the same chat, and return to them
            after switching topics. Earlier details stay with their request.
          </p>
        </section>
        <section>
          <h2>Review the right request</h2>
          <p>
            Reopen an existing draft or event to review its current details and
            status. Creating an event or saving a draft still uses its review card.
          </p>
        </section>
        <section>
          <h2>Events from meeting emails</h2>
          <p>
            Prepare an event from a selected meeting email, check the extracted
            details, and confirm it when you’re ready.
          </p>
        </section>
        <a
          className="update-primary"
          href="https://mail.google.com/"
          target="_blank"
          rel="noreferrer">
          Open Gmail <span aria-hidden="true">↗</span>
        </a>
        <p className="muted">
          Open Threadly from your browser toolbar to get started. You can close
          this page whenever you’re ready.
        </p>
        <footer>
          <a
            href="https://threadly.au/privacy/"
            target="_blank"
            rel="noreferrer">
            Privacy
          </a>
          <span>
            Store-installed copies receive future releases through your
            browser’s extension updates. Developer ZIP installs need a one-time
            switch to the store version when it is available.
          </span>
        </footer>
      </article>
    </main>
  )
}
