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
          <h2>Stay signed in</h2>
          <p>
            Reload Threadly or restart your browser without signing in again.
            Your login can renew for up to 30 days after sign-in.
          </p>
        </section>
        <section>
          <h2>A smoother return</h2>
          <p>
            A temporary connection problem keeps your login ready to retry.
            Signing out removes it from this browser.
          </p>
        </section>
        <section>
          <h2>Updating from an older version?</h2>
          <p>
            Sign in once if prompted. Threadly will remember that login across
            future reloads and browser restarts.
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
