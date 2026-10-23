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
        <p className="update-intro">
          A gentle cue for possible recent security alerts.
        </p>
        <section>
          <h2>Notice recent security alerts</h2>
          <p>
            A subtle glow fades away before a compact recognition check appears
            for possible recent security alerts. This is a subject cue only:
            Threadly has not verified the sender. No links open and no security
            actions are taken.
          </p>
        </section>
        <section>
          <h2>Recognise or dismiss, once per message</h2>
          <p>
            Unacknowledged mail received within the last 24 hours is eligible,
            including mail that arrived before this update. Recognised or
            dismissed messages stay quiet for 30 days. This feature saves only
            opaque references, timestamps and your response locally; it stores
            no email text. Hide Inbox marks in Settings to hide the cue.
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
