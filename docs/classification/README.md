# Email classification badge API

**Live backend handoff, 7 October 2026.** Claude Haiku 4.5 classification is deployed
and enabled at `https://api.threadly.au` from `release/backend` commit `7b2c277`.
[PR #105](https://github.com/bhowmikdham/Threadly/pull/105) contains the service.
Five authorized live Gmail threads passed the response-contract checks; the live
concurrency check admitted two calls and rejected two with 429/Retry-After.
See the [deployment evidence and limitations](ROLLOUT-2026-10-07.md).
The Nova Micro Flow is retired. The frontend team owns calling this service,
request scheduling and badge rendering; no frontend implementation was deployed.

## Release baseline and scope

The earlier audit inspected old `main`. The release backend already has Bedrock,
revocable sessions, parsed sender addresses and bounded live Gmail retrieval.
[On-demand Gmail](../on-demand-gmail.md) supersedes the earlier mailbox-import,
classification-table, background queue and cached-thread projection proposal.

This service stores no source text, labels, classification cache, user corrections
or jobs. No database migration is required. Existing `threads.needs_reply` and
list/filter behavior are unchanged. Durable classification, saved overrides and
mailbox-wide filters are future work, not prerequisites for these badges.

## Request and response

```http
POST /threads/{gmail_thread_id}/classification
Authorization: Bearer <Threadly access token>
Content-Type: application/json

{"time_zone":"Australia/Melbourne"}
```

Use a verified Gmail API thread ID from the existing integration. The backend
fetches mail with the authenticated user's credentials. Never send email bodies,
subjects, account IDs, model IDs or predicted labels; extra request keys fail.
`time_zone` accepts IANA zones and defaults to UTC. Supply the user's zone when known.

```json
{
  "schema_version": "email-classification-with-action.v1",
  "thread_id": "abc123",
  "source_message_ids": ["def456"],
  "source_fingerprint": "<backend fingerprint>",
  "status": "classified",
  "labels": {
    "needs_reply": true,
    "priority": "Medium",
    "category": "legal_contracts",
    "action": "review"
  },
  "evidence": {
    "needs_reply": ["def456"], "priority": ["def456"],
    "category": ["def456"], "action": ["def456"]
  },
  "reason_codes": ["unanswered_request"],
  "evaluated_at": "2026-10-06T02:00:00Z",
  "valid_until": "2026-10-06T02:05:00Z",
  "time_zone": "Australia/Melbourne",
  "release_id": "<backend release digest>",
  "source": "live_gmail",
  "coverage": "cleaned_text_only",
  "persisted": false
}
```

Results describe the current conversation, not each message independently.
Category/priority/action preserve BERT label names, not necessarily its predictions:
BERT used single-email subject/body inputs. Binary reply is an additional decision,
not a conversion from action. Evidence IDs belong to the fetched thread; source
membership is checked, but semantic grounding requires model evaluation.
No softmax/confidence values are fabricated. Successful responses use `Cache-Control: no-store`.

| Field | Backend values | Display labels |
|---|---|---|
| `needs_reply` | `true`, `false` | Reply, No Reply |
| `priority` | `High`, `Medium`, `Low` | High, Medium, Low |
| `category` | `finance_payments`, `hr`, `it`, `legal_contracts`, `meeting_scheduling`, `other`, `projects` | Finance, HR, IT, Legal, Meeting, Other, Projects |
| `action` | `approve`, `attend`, `complete_submit`, `edit`, `no_action`, `reply`, `review` | Approve, Attend, Complete/Submit, Edit, No Action, Reply, Review |

Show only the badges needed by the product. Retain action independently of the
binary reply badge for BERT compatibility. Labels never authorize sending,
inserting, approving, paying or booking anything.

The separate BERT intent model predicts `summarise`, `compose`, `reply` and
`schedule` from user prompts. It remains part of assistant routing, not email badge
classification. Its `other` fallback is derived, unlike the trained email category
`other`. No trained binary reply model was found; `needs_reply` is a new independent
decision. The [BERT artifact snapshot](../../ml/evals/classification/bert-labels.json)
records all four models, their native label IDs and source hashes.

## Loading and failures

| Outcome | UI behavior |
|---|---|
| Request in progress | Loading/unclassified; no default No Reply/Low/Other. |
| 200 `classified` | Render labels; false is a real No Reply prediction. |
| 200 `needs_review` | Null labels/evidence. Show unclassified/needs review; reason includes insufficient_context, ambiguous_purpose or context_limit. |
| 200 `skipped` | Null labels/evidence; not_in_inbox means no eligible inbox message. |
| 409 `classification_source_changed` | Clear obsolete badges, refetch visible thread, offer/retry one fresh classification. |
| 409 `classification_release_changed` or `classification_expired` | Discard; reclassify if still visible. |
| 429 `classification_busy` | Respect Retry-After: 5 with bounded backoff. |
| 503 `classification_disabled` / `classification_not_configured` | Hide unavailable badges; no repeated retry. |
| 502 `classification_output_invalid` | No badges; invalid JSON/labels/evidence or incomplete output. |
| 503 `classification_provider_unavailable` | No badges; permit deliberate retry. No automatic fallback. |
| 401/403/404 or Google connection errors | Existing sign-in/reconnect/source-missing UX. |
| 422 | Correct invalid input/ID/zone or oversized Gmail source before retry. |

Errors use the existing envelope:
`{"error":{"code":"classification_output_invalid","message":"...","detail":null}}`.
An error contains no successful-label fields.

Use at most two concurrent badge requests per client initially, for visible or
selected threads only. The server caps model calls per API process (default two),
not across the deployment. Repeated requests may invoke the model again: there is
no shared cache, single-flight deduplication or exactly-once billing guarantee.
Do not reclassify on every component render.

Keep results in view memory keyed by account, thread, timezone and fingerprint.
Use a local request-generation counter and discard older responses. Clear badges
on new mail, sending, draft changes, account change/disconnect or valid_until.
Refresh only while visible. An obsolete badge must not remain actionable during refresh.

valid_until is a maximum display lifetime, not a guarantee Gmail stayed unchanged.
The backend independently refetches after inference and rejects changed fingerprints.
Gmail can change immediately after that final read; there is no atomic transaction
across Gmail and the browser. Badges are never authority for external writes.

The existing server-side needs_reply filter is not populated by this service.
Client badge filtering covers the classified visible page, not the whole mailbox.
Pending/failed items remain unclassified.

## Context and privacy bounds

- Live retrieval keeps the release's existing 200-message/8 MB bounds.
- Model context: at most 50 non-draft messages, 24,000 subject/body characters and
  64,000 serialized input bytes including schema/metadata. Oversized context returns
  needs review without silently truncating it.
- Exclude Draft, Spam and Trash. Require one non-draft inbox message; include sent
  messages in that thread so previous responses can resolve outstanding requests.
- Cleaned text only; attachments, stripped quotes and signatures are unavailable.
  Missing sender/timestamp/usable text returns needs review. The model must abstain
  when unavailable context could materially change a label.
- Primary account address maps to ME; other addresses use stable participant aliases.
  SENT metadata marks account-sent messages. Full send-as alias/Bcc interpretation
  is not implemented; ambiguous responsibility must remain uncertain.
- Subject/body identifiers use the existing limited masking policy; trusted
  timestamps/source IDs remain exact. This is not full de-identification.
- No database transaction stays open during Gmail/Bedrock IO. Session revocation
  and account identity are rechecked before returning results.

## Configuration and evaluation

```text
GMAIL_SOURCE_MODE=on_demand
CLASSIFICATION_ENABLED=false
CLASSIFICATION_MODEL_ID=au.anthropic.claude-haiku-4-5-20251001-v1:0
CLASSIFICATION_TRANSPORT=bedrock_flow
CLASSIFICATION_FLOW_MANIFEST=
CLASSIFICATION_MAX_CONCURRENCY=2
CLASSIFICATION_VALID_SECONDS=300
BEDROCK_REGION=ap-southeast-2
BEDROCK_MAIL_PROCESSING_ACKNOWLEDGED=false
```

For visual Flows, load the verified `haiku.target.json` into
CLASSIFICATION_FLOW_MANIFEST. It pins the model, managed prompt version, Flow version,
alias, execution role, graph hash and bounds. Runtime Flow validation accepts only
the selected Australian Haiku 4.5 profile; stale Nova targets are rejected. Enable
only after deployment acceptance. Existing assistant Flow registries remain separate.
The SDK's default transport remains `converse` for explicit baseline compatibility;
the example configuration above opts into Flows. Converse uses the dedicated
CLASSIFICATION_MODEL_ID; a Flow uses its manifest's model and ignores that setting.
There is no automatic provider/model fallback.

Both paths cap prompt output at 1,500 tokens and validate JSON/evidence identically.
Flow calls require terminal SUCCESS and verify the alias before and after execution.
They read the numbered prompt and graph to reject release drift. Flow reads have
a 10-second SDK read timeout within the manifest's overall deadline (default 90s);
Converse uses BEDROCK_READ_TIMEOUT_S. No application retry or repair call is made;
Bedrock may retry internal work, so billing is not exactly once. Cancellation keeps
the process concurrency slot until the SDK worker closes. Provider errors expose
no raw details. See [visual Flow setup](VISUAL-FLOWS.md).

The release digest binds model, region, prompt, schema, policy, timeout, validity
and the full Flow target when configured.
The prompt is `backend/app/classification/prompt.txt`; synthetic cases and the
prompt/schema manifest are in `ml/evals/classification/`.

From `backend/`, validate fixture contracts without AWS:

```bash
python -m app.classification.evaluate --fixtures ../ml/evals/classification/v1.json
```

Use `--predictions /path/to/predictions.json` to replay a JSON object mapping every
case ID to its raw parsed model decision. With model access configured, explicit
`--live` invokes only these synthetic cases and reports per-field/joint correctness,
invalid outputs and predictions. Save the report's predictions member for replay.
The initial enabled release passed the bounded integration checks in the
[rollout record](ROLLOUT-2026-10-07.md). Production accuracy thresholds and a broader
held-out, human-labelled dataset remain quality work; neither this smoke test nor
mock/fixture passes establish production accuracy.

## Frontend artifacts

- [Backend deployment, activation and rollback](DEPLOYMENT.md).
- [Request schema](request.schema.json) and [response schema](response.schema.json),
  exported from runtime models; semantic state checks also execute in Python.
- [Synthetic frontend fixtures](frontend-fixtures.json) for classified, needs-review
  and skipped UI states. No fake-provider mode is enabled in production.
- [TypeScript types and display mappings](types.ts).
- [Verification record](VERIFICATION.md).
- [Visual Flow provisioning and candidate evaluation](VISUAL-FLOWS.md).
