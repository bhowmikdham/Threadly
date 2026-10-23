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
        <p className="update-intro">Make your inbox easier to scan.</p>
        <section>
          <h2>Your inbox, with clearer marks</h2>
          <p>
            Priority chevrons, reply marks and categories help you spot what
            needs attention. Hover, focus or tap any mark for its meaning.
          </p>
        </section>
        <section>
          <h2>Prefer words?</h2>
          <p>
            Open Settings to preview words beside the reply and category icons.
            You can hide inbox marks at any time. Your messages stay in place.
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
