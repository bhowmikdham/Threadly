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

Verification before PR: 287 frontend unit tests and TypeScript passed; full
localhost packaged browser suite 60 passed (HTTPS-only smoke separately). Final
public build passed five focused browser tests including both snippet structures,
security-awareness interactions, missing/invalid reused-row identities, removed
row structure, explicit light/dark/narrow layouts and the public-origin smoke.
Backend focused classification/metadata/assets suite: 71 passed; Ruff clean.
Full PostgreSQL and deployment/Flow checks run in companion PR144's backend CI.
No real mailbox/provider/model test was run.

Independent source review found one P2: an existing cue survived when Gmail
cleared a reused row's identity/subject structure. The fix removes cue/glow before
render's early return; synthetic missing/invalid ID and removed-line regressions
pass. No other concrete blocking findings. Additional review fixes fence late
responses to the current session, bound in-page suppression, preserve the first
concurrent acknowledgement and purge expired references on worker startup/use.
Expiry is logical at 30 days and physical cleanup happens on startup/next use.

Release isolation: currently deployed backend is 8970bd4b7cb8ce504a672b6eb37d275ef8b14971,
public-launch. release/backend additionally contains unshipped calendar PR140.
Companion PR144 targets release/security-awareness based on deployed 8970bd4,
using the same complete backend CI gates and immutable tested-tree requirement.
The integration history is preserved. Backend rollout must leave the current
Caddy container, independently deployed logo site and download mounts unchanged.
Frontend version is 0.2.7 from released 0.2.6. Both releases remain subject to
CI, reviewed merges and deployed/public artifact acceptance.
