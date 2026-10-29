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
          Clearer replies, more natural voice conversations and account-access
          cues.
        </p>
        <section>
          <h2>Review replies while speaking</h2>
          <p>
            Reply drafts stay visible and editable when Threadly answers aloud.
            Quick answers speak directly, with a brief progress cue only when
            work takes longer. Review the draft and recipient before using it.
          </p>
        </section>
        <section>
          <h2>Recognise account-access mail</h2>
          <p>
            Recent sign-in codes and access confirmations can show a recognition
            check. Recognising an event removes only its security concern from
            priority when a supported assessment is available; other important
            work stays important. This does not verify the sender or mark mail
            safe. Recognised or dismissed messages stay quiet for 30 days.
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
