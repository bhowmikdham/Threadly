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
        <p className="update-intro">A calmer inbox, with shorter previews.</p>
        <section>
          <h2>Shorter previews, clear subjects</h2>
          <p>
            Preview snippets after each subject take up less space. The original
            subject and message stay intact, with priority, reply and category
            marks still available.
          </p>
        </section>
        <section>
          <h2>Your display choices stay with you</h2>
          <p>
            Your words/icons preference is unchanged. Hover, focus or tap a mark
            for its meaning, now with a normal pointer. Hide marks in Settings
            to return to Gmail’s original preview layout.
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
