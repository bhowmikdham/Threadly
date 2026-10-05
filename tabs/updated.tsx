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
        <p className="update-intro">A calmer space for your calendar.</p>
        <section>
          <h2>Less clutter, clearer controls</h2>
          <p>
            Find your connections in the conversation menu. Compact skill pills
            and expandable settings keep the essentials close.
          </p>
        </section>
        <section>
          <h2>Calendar checks you can fix</h2>
          <p>
            See which calendar could not be checked and review your selections
            directly from the conversation.
          </p>
        </section>
        <section>
          <h2>You’re in control</h2>
          <p>
            Review calendars after reconnecting, choose your working hours, and
            save your changes. Threadly keeps your calendar choices explicit.
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
