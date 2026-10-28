# Security recognition candidate — prompt 1.0.2

This is a local implementation candidate, not a deployed/published release.
No actual Hertz mail was supplied or available through a connected read route.
The examples in `ml/evals/classification/security-recognition-v1.json` are
representative synthetic fixtures, including Hertz-labelled access versus booking
mail; they do not establish what the missed message said.

The released classifier returns an aggregate priority with no counterfactual
baseline. The frontend's recognition acknowledgement only hides its question;
there is no safe way to infer independent importance from that aggregate label
or a displayed subject. Blanket High-to-Low conversion would lose real urgency.

The optional `security_priority` decision now identifies one source alias and a
`priority_if_recognised` plus evidence aliases. Prompt 1.0.2 asks for this only for
a concrete security/access event in the newest incoming message, including
sign-in codes/links/confirmations. It asks the model to preserve independent work,
deadlines/consequences and earlier unresolved requests across the whole
conversation. Ordinary bookings, reminders, marketing and uncertain baselines
omit/null the object. This adds no second model call or source read.

Deterministic validators reject unknown/duplicate references, extra fields,
unclassified baselines and a baseline above the normal priority. The service
maps aliases to owned Gmail IDs and exposes the optional response object only
when its event is the exact provider-dated newest incoming inbox message. Existing
owner/account-version, source fingerprint, release and expiration fences remain.
The response object contains only a message ID, a fixed priority enum and source
references; no codes/links/devices/locations/text or authentication claim.

The companion frontend overlays this priority only after a confirmed local
`recognised` acknowledgement. Dismissed/unrecognised mail retains the classifier's
normal priority. Missing/invalid older-backend data does not trigger a guessed
downgrade. No durable server override, mail record, cache, migration or Gmail
write is introduced. Protected v1 HMAC acknowledgements remain the only durable
recognition state; no body, subject, sender or event details enter them.

## Verification and remaining gates

- Focused backend classification suite: **120 passed, 13 skipped**. The 13 tests
  require unavailable PostgreSQL. No database/container changes were attempted.
- Ruff check of `backend/app/classification` and the new test: passed.
- Eight synthetic fixture contracts validate offline. Scoring now includes the
  counterfactual object; a deliberately wrong Low baseline for independent legal
  urgency fails replay scoring even though the four ordinary labels match.
- Fake-provider tests prove two existing source reads/one model call, exact event
  binding, current/outgoing source suppression, unchanged ordinary output, and
  invalid-baseline rejection. Existing source/owner/release fences also pass.
- No paid inference, live Gmail, provisioning, deployment, PR or publication run.
  Authored expected fixtures are contract tests, not model-quality evidence.

**Required before rollout:** evaluate prompt 1.0.2 on held-out synthetic cases
using the chosen model, provision/version its managed prompt/Flow through the
existing release process, and perform exact-head acceptance. Existing managed
prompt 1.0.1 must not be silently reused: runtime Flow verification compares the
numbered prompt to source and rejects drift. The release manifest explicitly
marks model evaluation pending. This work does not authorize those operations.

Isolation: branch `fix/security-recognition` based on released maintenance backend
`1cb73283120b753d4ae01359d2a4d696636093c6` in task-2. Voice/reply fixes live in
separate clones; no shared source files or test database were changed.
