# Security awareness implementation checkpoint

Status: implemented locally, not released. Backend companion adds only optional
latest_incoming metadata from the existing owner-checked classification source
read, based on Gmail internalDate. No new provider/model request or scope.

The content script examines only the displayed subject transiently using explicit
English security-alert/sign-in/password-change cues with forwarded/marketing
exclusions. It does not authenticate the alert. A fixed question and unverified
sender explanation appear with recognise/dismiss controls, a static cue and two
finite glow cycles (none for reduced motion). No mail/security action is taken.
Existing subject/snippet content, labels, words/icons, native controls are preserved.

The background binds metadata to the authenticated mailbox, origin/user and exact
last Gmail message ID. First classifier use after feature activation establishes
an account baseline before source retrieval. Only receipts strictly after that
baseline, not in the future and less than 24 hours old can prompt. Existing mail
on first install, unknown identity/timestamp/order, old unread mail, and account
mismatch fail closed. A new incoming message in an old thread has its own key.

Protected local storage retains a random HMAC privacy salt, account-key/baseline/
last-use timestamps and message-key/receipt/ack timestamps plus recognised or
dismissed state. No email body, subject, sender, snippet, account address, raw
message/thread ID or free text enters this durable feature state. Scope and IDs
are HMAC-SHA256 hashed. Existing classifier session cache remains memory-only;
its additive response metadata is not copied into local storage. Content scripts
cannot access protected local/session storage. Saves are serialised, validated,
allowlisted and confirmed before a successful acknowledgement is shown.

Acknowledgements expire after 30 days with a global 1000-entry cap; active entries
are never evicted to admit another question. At capacity, new prompts stop.
Account baselines cap at 100 with 30-day inactivity expiry; reactivation resets
baseline to now. Expired acknowledgements cannot revive old mail due to the
24-hour and activation gates. Corrupt/unavailable storage and clock rollback
suppress awareness while ordinary inbox marks remain usable.

Local checks: 285 frontend unit tests, TypeScript, four focused packaged browser
tests (security awareness and existing row layouts), plus one separate public
origin smoke passed. Synthetic screenshots at 1440/640 inspected; current fixture
has a transparent body and is detected as dark, so verify explicit light/dark
fixtures before final acceptance. Backend focused classification/metadata/assets
suite: 70 passed; backend Ruff clean. Full backend PostgreSQL CI, full packaged
browser suite, independent source review, release CI/public artifact acceptance
and deployment remain pending. No real inbox/provider/model test was run.

Release isolation: currently deployed backend is 8970bd4b7cb8ce504a672b6eb37d275ef8b14971,
public-launch. release/backend additionally contains unshipped calendar PR140
(6ae527e). Companion is based on deployed 8970bd4 so it contains no new calendar
changes. Select a reviewed, merged maintenance release route from that baseline;
never deploy current release/backend head as part of this task. Frontend version
is prepared as 0.2.7 from released 0.2.6; no PR/push/merge/deploy has happened yet.
