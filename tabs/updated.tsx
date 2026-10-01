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
          A clearer view of your connections, right beside your inbox.
        </p>
        <section>
          <h2>Your connectors, in one place</h2>
          <p>
            Click Gmail or Google Calendar in the conversation menu to see its
            skills, tools and data sources.
          </p>
        </section>
        <section>
          <h2>Know what’s ready</h2>
          <p>
            See which permissions are ready, which need your attention and which
            actions aren’t available yet.
          </p>
        </section>
        <section>
          <h2>You’re in control</h2>
          <p>
            Reconnect your account, choose your calendars and working hours, or
            disconnect Google from Threadly.
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
