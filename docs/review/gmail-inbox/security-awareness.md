# Security awareness implementation checkpoint

Status: implemented locally, not released. Backend companion adds only optional
latest_incoming metadata from the existing owner-checked classification source
read, based on Gmail internalDate. No new provider/model request or scope.

The content script examines only the displayed subject transiently using explicit
English security-alert/sign-in/password-change cues with forwarded/marketing
exclusions. It does not authenticate the alert. A single refined row glow runs for 1400ms and fades fully, followed by a 160ms
quiet interval and a 180ms entrance for a compact recognition prompt. The prompt
reads “Do you recognise this email?” with an unverified-sender note and 36px
recognise/dismiss controls. Reduced motion skips glow, delay and entrance motion.
A bounded account/message presentation history prevents replay on row rerenders. No mail/security action is taken.
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
Companion PR144 passed 2459 PostgreSQL/backend tests, plus deployment/Flow gates
and 26 classification provisioning tests (8 subtests).
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
Frontend version is 0.2.7 from released 0.2.6. The backend is deployed and publicly verified from maintenance merge
1cb73283120b753d4ae01359d2a4d696636093c6, preserving schema, configuration, Caddy,
logo site hashes and downloads. The frontend remains unpublished pending a visual
checkpoint, exact-head CI and public artifact acceptance; public ZIP remains 0.2.6.

Final sequence validation records browser animation/prompt timestamps: one glow
starts and ends before prompt insertion; row animation is none when the prompt
appears; rerendering the same message adds no glow. A new reduced-motion message
shows the static prompt in under 1200ms (including fixture/bridge scheduling), with
no animation. Keyboard recognition, dismiss, reload, metadata/account failures,
narrow/light/dark layouts and original row controls pass. Synthetic full and
close-up screenshots were inspected before the requested visual checkpoint.

Post-reconnect reconciliation: the old process session could not be recovered;
a fresh run of the exact built/source revision passed all five focused browser
tests (17.2s). The completion gate now follows actual animationend and the
250ms delayed-start regression passes. The independent visual checkpoint and
synthetic captures are in [security-visuals](security-visuals/README.md).
