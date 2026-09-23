# Compose routing correction — 23 September 2026

## Observed failure and cause

The live staging smoke passed readiness, blocked mailbox sync and Calendar access,
but a standalone compose request failed with `invalid_route_output`. A synthetic
reproduction against the deployed Haiku profile returned a JSON Markdown fence
and put a named recipient in `parameters.recipient_refs`. The backend correctly
rejected the latter: the model cannot select recipients. This was a model-output
contract failure, not evidence of insufficient EC2 capacity.

## Runtime behavior

- `intent-preview-1.2.0` explicitly requires empty recipient references and null
  context ID. The backend continues to bind context, recipients and permissions.
- Router and draft parsers accept raw JSON or one complete `json`/unlabelled
  Markdown fence. They reject surrounding prose, partial fences, multiple objects,
  duplicate keys, non-finite constants, invalid schemas and invalid semantics.
  They never repair fields, drop invented recipients or retry an invalid route.
  Other workflow parsers are unchanged.
- `draft-artifact-1.1.0` receives only a boolean indicating whether recipients were
  selected. It is instructed not to ask for an already selected address, add an
  unsolicited feedback promise, or append a sign-off when no signature was requested.
  Actual addresses, reply targets and approval remain backend-owned.
- `bounded-reads-task-1.1.0` replaces outdated HELP text with capability-dependent
  descriptions. Sending and booking still require permissions, server controls and
  separate exact-payload approval. This change does not enable external writes.
- `lookup-draft-template-1.1.0` pins the updated shared draft/read contract.

Existing failed tasks stay failed for audit. Submit a fresh request ID after
upgrading. Queued tasks pinned to older releases may fail closed with
`release_unavailable`; do not rewrite their saved release hashes to make them run.
There are no migrations or environment changes for this correction.

## Replay and limits

`docs/evaluation/compose-routing-live-v1.json` records actual synthetic Haiku
results with prompt/schema/case hashes. Seven routing generations passed: compose
three times, summary, reply, availability and summary-plus-reply. One generated
compose draft passed the real artifact validator and content checks. No mailbox
was read and no email/event was sent or created. This is a targeted regression
replay, not a broad quality score or full provider-write acceptance.

From the backend directory, list cases without invoking a provider:

```sh
python -m app.planner.evaluate_routing
```

With Bedrock configured and both external-write flags disabled, the following
makes eight billed synthetic model calls:

```sh
python -m app.planner.evaluate_routing --live
```

The offline tests pin the committed evidence to current prompts/schemas, exercise
valid fences and malicious/malformed alternatives, and check recipient/source
validation remains intact. The full regression run passed 1,113 tests and identified one stale lookup-contract
fixture. After updating that versioned fixture and the final draft prompt, all 244
affected tests passed, including the fixed case and the new live-evidence hash test.
Ruff, the MVP asset check and whitespace checks passed. Deployment evidence is
recorded in the PR; a successful direct-model replay alone does not prove the
API/worker deployment has loaded the new code.
