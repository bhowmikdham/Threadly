# Indirect access recognition and priority restoration candidate

Local implementation on `fix/security-recognition`, based on frontend
`4fe4f323ec31de35184f70d4eaad056efdd39bef` (released recent-alert eligibility).
The backend companion is isolated in `task-2/Threadly-security-backend`, based
on released maintenance `1cb7328`. No shared source checkout was edited.

## Reproduction and causes

No actual Hertz email was supplied or available through an authorized connected
read route. Personal Context returned unavailable; the local memory store had no
Threadly entries. All mail in new tests is representative synthetic data.

Evaluating the released `possibleSecuritySubject` function returned false for
all four synthetic examples: `Hertz: Confirm your sign-in`, `Your Hertz sign-in
verification code`, `Account access verification`, and `Sign-in successful`.
Its anchored explicit-alert/new-sign-in patterns excluded routine access
verification. The renderer required that matcher even when owned classifier
context could identify an access event.

The separate recognition bug is structural: acknowledgements only suppressed
cue/glow, while the row always rendered `r.value.labels`. The backend returned
only aggregate priority, so lowering every recognised High to Low would erase
independent work/deadlines. The new companion returns an optional source-bound
whole-conversation `priority_if_recognised`, with Low/Medium/High permitted.

## Changed flow

- Existing explicit subject cues remain. Indirect access/verification wording
  now qualifies, requiring access context and excluding ordinary rental/booking
  subjects. A generic verification code needs the backend's semantic access cue.
- The companion's optional evidence-backed baseline also acts as an indirect
  cue, with the existing forwarded/marketing/length/control-character exclusions.
- The same provider-timestamp, exact-last-message, under-24-hour, account/session,
  storage, capacity, dedup, reduced-motion and glow-before-question fences apply.
  The CSS animation sequence and native Gmail controls are unchanged.
- Recognition is returned separately from prompt eligibility. After confirmed
  storage, every displayed matching row overlays the backend baseline immediately;
  its priority tooltip refreshes too. Independent High or Medium remains so.
  Dismissal/unrecognised mail retains ordinary classifier priority.
- Saved v1 HMAC state survives reload/service-worker restart. Cross-tab enums and
  in-page first-disposition memory fence late classifier replies. Raw classifier
  cache entries remain unchanged, so each fresh/cached response rechecks recognition.
- The protected memory-only classifier cache key now includes server, user and
  mailbox, preventing same-ID cross-server/mailbox reuse. Old unscoped cache keys
  are ignored; protected durable v1 acknowledgements/salt are not reset.
- No subject/body/sender/code/location/device details are persisted. No extra
  Gmail/model request, extension permission, server override or real mail action.
  Older/missing/invalid baseline data preserves the original badge.

## Checks and limits

`npm run typecheck`: passed. `npm test`: **329 passed**, including the real
content-script and background bridge under synthetic adapters. Covered cases
include failed save, immediate update, same/new row, stale classifier responses,
reload/worker restart, first conflicting disposition, dismissed versus recognised,
independent Low/Medium/High, 24-hour/30-day boundaries, capacity, storage failure,
account/server isolation, metadata binding and privacy allowlists.

Backend companion: **120 passed, 13 PostgreSQL-dependent skipped**, Ruff clean;
eight authored counterfactual fixture contracts validate offline. Model quality
has not been measured. See companion `docs/classification/SECURITY-RECOGNITION.md`.

`npm run build` and its diagnostic `--verbose` attempt initially stopped at
Parcel's native `lmdb.open` cache initialization with **Operation not permitted**
in the sandbox. Parent then explicitly authorized normal escalation; automatic
review approved `npm run build`, which **passed**. No dependency/cache bypass or
production change was used. The build printed the existing optional svgo warning.

After the voice task released its browser slot, the default sandbox Playwright
attempt failed at Chromium's Crashpad/Mach bootstrap permissions before tests ran.
Normal escalation was approved. Packaged command:

```
./node_modules/.bin/playwright test tests/e2e/security-awareness.spec.ts tests/e2e/inbox-row-layout.spec.ts
```

**5 passed (25.2s)**, with one worker and disposable headless profiles. It checks
actual glow completion before prompt reveal, no repeated glow, reduced motion,
keyboard recognition, Low/Medium/High baselines, reload, dismissal, metadata and
account failures, forwarded/marketing filtering, original row actions, sibling/
nested snippet structures, light/dark/narrow layouts. All contexts closed in
teardown. No shared interactive browser was used. The browser slot is released.
No automatic review rejected an action; sandbox failures have been resolved.

The parent status message to `01a1728f-b61e-76c7-8fe9-593a2ae640f9` failed
`thread not found`; isolation/status were delivered to the parallel voice task.
Parent subsequently confirmed isolation and authorized independent non-browser
work. No overlapping source file edits exist; integration remains parent-owned.

Before rollout, the updated backend managed prompt/Flow needs versioned
provisioning and held-out model acceptance. This task authorized neither paid
inference nor provisioning/deployment. The implementation is local and unshipped;
no PR, merge, deployment or publication was performed.
