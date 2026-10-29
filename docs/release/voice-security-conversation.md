# Voice, reply and security-recognition release candidate

This candidate combines the scoped reply speaker/source fix, voice draft visibility,
indirect account-access cues/recognised priority and natural conversation changes.
Runtime parent `bc81b1d63c166b0488c8ca7fb66d319eb2b19489` passed the complete synthetic
backend suite with disposable PostgreSQL: **2,504 passed, zero skipped**,484.78s,
plus Ruff. Its companion frontend runtime `985d0c0` passed349 unit and22 packaged
browser cases, typecheck and build. The source branches and shared dirty checkout
are preserved. Release packaging increments the extension to0.2.9 with new notes.

## Integration boundary

The backend PR targets the existing narrow maintenance branch
`release/security-awareness`, whose head `1cb7328` is the tested parent. Do not retarget
it to `release/backend`: that branch contains separate email-to-calendar work, and
the maintenance branch intentionally excludes it. The unrelated draft Haiku5.5
migration PR is outside this release. Keep the existing Australian Haiku4.5 profile.
The frontend PR targets `frontend`, whose head remains the tested `4fe4f32` parent.
No merge or publication of frontend is permitted before backend gates: pushing
`frontend` triggers the automatic public ZIP feed.

## Blocking prerequisites

Classification prompt revision1.0.2 adds a source-bound `security_priority` decision.
The runtime verifier compares the numbered managed prompt's full variant against
this candidate. A previous prompt/Flow target will fail the release guard. Never
bypass the guard, silently select Converse, or remove classification in order to
claim the complete requested release. The deployment needs a reviewed new numbered
prompt and Flow/version/alias, its exact manifest and scoped caller permission.

`infra/bedrock/classification_flows.py --render-only` is offline. `--prepare-assets`
creates managed prompt resources; the normal launcher creates/resumes a dedicated
role stack and publishes Flow versions/aliases. Those are cloud mutations, and the
fresh role/caller target changes security access. Existing roles/aliases must remain
available for rollback. A reviewed execution-role reuse design may narrow the IAM
change, but cannot be assumed without inspecting actual policies. No wildcard access,
quota increase, region/model swap or terms acceptance is part of this candidate.

Model semantics for classification1.0.2, reply generation1.2.3 and conversation
`1.8.11+natural-voice.1` have not received a new live acceptance run. Deterministic
tests and CI do not establish their quality. Separate approval for bounded paid
inference and exact provisioning/IAM changes is required before dependent deployment.
Historical evaluation budget approvals are spent/closed and cannot be reused.

## Proposed bounded model acceptance

Use synthetic identities/mail only, the already selected Australian Haiku4.5
profile and exact candidate assets. No Gmail reads/writes, Calendar execution,
real TTS/STT calls or private mail content. Use an isolated local database if a
scenario needs persistence, with every external write adapter denied. Save a new
locked budget ledger before the first dispatch, count every provider attempt,
disable transparent retries and stop on failure rather than inventing completions.

1. Twenty classification cases: the existing12 category/action/reply regressions
   and8 security-recognition cases. Review every output; require valid typed evidence,
   recognition source binding, ordinary verification exclusions and preservation of
   independent urgency. Run through the new published Flow, not a different transport.
2. Six reply cases: inbound reply, owner follow-up, explicitly selected older message,
   missing recipient/source clarification, quoted malicious role instruction and a
   genuinely independent new compose request. Review owner/correspondent roles,
   facts, target, unresolved fields and no fabricated save/send claim. Include the
   conversation decision and actual draft generation, not only the final prompt.
3. Six conversation sequences: named greeting plus reciprocal follow-up, repeated
   greeting, greeting before substantive work, pending body/title ambiguity, correction
   and spoken cancellation. Clear bounded greetings should make zero model calls;
   other turns must retain the pinned source/pending work and report actual outcomes.

Proposed ceiling: **48 total billed attempts**, at most six per end-to-end scenario,
no retries outside that ceiling, paced to the actual account quota (provisionally at
most six/minute, one concurrent invocation). Reserve **US$0.05 per attempt**, with a
hard **US$2.40 total budget** and20-minute wall limit. Dispatch only after a conservative
input-token bound and requested max output (at most2,500) fit the reservation and
current regional pricing/Flow node charges/taxes. If not, stop for a revised plan;
do not truncate the actual prompt or silently raise limits. Published Haiku4.5 base
rates areUS$1/MTok input andUS$5/MTok output; this is a planning reference, not a quote
for the account's AU Bedrock route. Inspect current regional billing before spending.
No unused allowance carries into an unapproved retry or later test campaign.

Existing evaluation scripts are not automatically safe for this allowance: the
classification evaluator can retry in its provider, and historical chat harnesses
pin older approval tokens/ledgers. Prepare and offline-test a new pre-dispatch guard
for this approved scope before using any live mode. A failed quality gate blocks
release; fixing prompts requires rerunning only the changed approved cases within
the original remaining allowance, or obtaining a new allowance when exhausted.

Pricing references: [Haiku4.5](https://platform.claude.com/docs/en/models/haiku-4-5/overview),
[Bedrock pricing](https://aws.amazon.com/bedrock/pricing/).

## Release order after prerequisites pass

1. Restore the configured Threadly AWS session and read the protected host's current
   commit/mode/schema/images, permitted feature configuration and current Flow target.
   Compare against the maintenance parent; isolate any newly discovered drift rather
   than resetting branches or including unrelated changes. Record the rollback image,
   deployment metadata, exact Flow manifest and narrowly scoped policies privately.
2. Require green exact-head PR CI, model acceptance and verified versioned targets.
   Merge only the reviewed scoped backend PR into the correct maintenance branch;
   verify tested/deployable tree identity or rerun full CI on the merge commit.
3. Use a backend-only rollout preserving current public Compose overlays, Caddy,
   website mounts and unrelated services. Build/preflight before drain; take a
   protected PostgreSQL backup, check schema compatibility, and recreate only API
   and its matching backend workers. Do not change write settings, scopes or audiences.
4. Verify public TLS health/readiness and worker health, plus synthetic candidate
   contract checks. Do not send a message or create an event as a smoke test. If model
   validation would be needed again, charge only to the explicit remaining allowance.
5. Merge frontend only when its exact-head CI is green and the compatible backend is
   ready. Verify its public-origin package, source commit, version0.2.9 and artifact
   checksum; wait for the website updater, then independently verify served ZIP bytes
   against the same CI release metadata. A published feed alone is not deployment.
6. On failure, preserve diagnostics privately and restore the saved compatible API/
   worker image/configuration and previous Flow target. Do not downgrade DB or restore
   a dump blindly. For a bad public ZIP, stop its updater and atomically restore its
   recorded previous ZIP/metadata using the runbook, then publish a reviewed forward fix.

Current authorization covers the scoped PR/CI/integration/deployment work, but not
paid inference or expanded security access. No such calls or IAM changes have run.
