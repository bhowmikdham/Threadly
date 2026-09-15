# Concise summary correction

Branch `codex/summary-quality`, based on `c190126` (merged PR #15).
Code and contract replay are implemented. Live Haiku output quality is **not yet
verified**, and the existing AWS prototype has not been changed by this PR.

## Diagnosis from the reported console example

The screenshot's Input → Generate → Output nodes all completed. Its response uses
`operation`, `status`, `text`, `evidence_ids`, `questions`, `assumptions` and
`proposed_slot_ids`, matching the original **prototype** envelope in
`infra/bedrock/cloudshell_flows.py`. That summary prompt explicitly asks for separate
decisions, requests, unresolved questions and partial coverage. It encourages a
report rather than the short summary the product needs. The submitted object uses
`user_request` and `messages`, while the prototype documentation uses `instruction`
and `sources`; no explicit source IDs were supplied in the screenshot.

Observed faults:

- The payment blocker and concrete next step are buried beneath secondary terms.
- A supplier's offer to issue an invoice becomes a stronger “decision.”
- The same payment-status question appears in multiple fields.
- The response supplies assumptions not established by the source and gives
  inconsistent completeness statements.
- A `message_1` evidence ID appears despite no such ID being supplied.
- Markdown fences surround JSON that downstream strict validation would reject.

These are prompt, input-contract, output-validation and presentation problems.
The trace provides no evidence of EC2 resource exhaustion or an infrastructure
failure for this invocation. The console is invoking a managed model through a
Prompt node; resizing the application EC2 host is not a remedy for these specific
content defects. More scheduling/approval nodes are also unnecessary for this
single-source summary. Broader application context/integration remains unfinished,
but the submitted text already contains enough information for a useful summary.
[AWS Flow node documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/flows-nodes.html)
describes Prompt output as model-generated text and Output as returning that data;
a completed graph does not establish factual relevance or schema compliance.

## Changed contract

`assistant/summary_policy.py` is the shared dependency-free policy source.
`assistant/summary_quality.py` applies the same policy to application summary
requests and validates the resulting object before using the existing evidence
and artifact builder. The public artifact schema does not change.

| Field | Behavior |
|---|---|
| `overview` | Issue/outcome/blocker first, 1–3 sentences; prompt target 35–65 words, hard limit 80 |
| `decisions` | At most 3 explicit agreements; offers are not decisions |
| `actions` | At most 3 concrete next steps supported by numbered source references |
| `open_questions` | At most 2 material unanswered source questions, not invented missing-data checklists |
| All fields | 180 words maximum, each list item at most 35 words, no exact normalized duplicates |

The prompt preserves attribution, forwarded-message boundaries, later updates,
conflicts and relative dates. It separates ticket identifiers from order identifiers.
It does not ask the model to invent completeness, coverage or ownership assumptions.
Raw JSON is required; fenced JSON, extra fields, invalid/duplicate/out-of-scope
source numbers and oversized outputs fail closed. No silent truncation, “repair”
call or automatic acceptance of a verbose result is introduced.

The hard checks enforce size, shape, exact repetition and citation scope. They
**cannot establish that every claim is true**, detect all paraphrased repetition,
or prove that the most relevant fact leads the answer. Human review against the
source remains necessary. A contract failure is `invalid_summary_output`, not a
successful partial result. Large explicit detail requests remain bounded by the
same contract; a separate detailed-summary mode is not installed by this change.

Example of the intended result, using **synthetic replacements** for the reported
people/organizations/identifiers:

> Cedar Catering reports that last year's invoice INV-2047 remains unpaid and wants
> its payment status confirmed before proceeding with a new order. It can issue a
> tax invoice to the Student Activities Club, with payment after internal processing.
>
> Next step: Check invoice INV-2047's payment status with accounts.

This is a human-authored reference, not an observed Haiku result. Actual email
contents, account numbers and screenshot images are not copied into public fixtures.

## Runtime lifecycle and compatibility

```mermaid
flowchart LR
    T[Accept task and pin quality release] --> R[Existing intent routing]
    R --> C[Owned saved context or one mapped message]
    C --> P[Concise summary policy and numbered sources]
    P --> M[Native model adapter or published runtime Flow]
    M --> V[Word, shape, repetition and source validation]
    V --> A[Existing summary artifact and backend coverage]
    A --> UI[Overview and optional action details]
```

New tasks wrap the existing native/Flow release in `summary-quality-task-1.0.0`.
UI-context tasks retain their outer `ui-context-task-1.0.0` wrapper. Worker dispatch
unwraps these pinned policies before choosing generation; no active registry change
can silently retarget a queued Flow. Historical summary, contextual, UI and Flow
releases continue to use their original prompt and validator. Tests include an
older verbose result that remains valid under its original contract.

No schema migration or new dependency. Deploy API and worker together, drain/cancel
new quality-release jobs before rolling back to an older worker, and preserve the
provider configuration used by old releases. Existing artifact IDs, draft envelopes,
request replay hashes and external-action boundaries remain unchanged. Reply,
compose, exact native message quotes and routing prompts are not rewritten.

The **runtime** Flow graph from PR #14 still receives the backend's complete prompt
through `{{request}}`; its graph hash does not change. The existing three-operation
smoke evaluator now uses the new summary contract. Merely merging this PR does not
redeploy the EC2 application or edit any existing console Flow.

## One new CloudShell summary experiment

`infra/bedrock/cloudshell_summary.py` creates/resumes an isolated CloudFormation
stack containing **one summary Flow and one restricted role**. It uses the selected
Australian Haiku 4.5 profile, temperature 0, and maximum 1,200 output tokens. Stack
and Flow names include the prompt/graph content hash. Existing stacks/Flows are not
updated or deleted. Preparation does not invoke a model. Clicking Run afterward
incurs model usage.

This standalone console experiment includes an explicit adapter for `instruction`
(or `user_request`) and a `messages` array, so the screenshot's input shape works.
Source numbers are defined as 1-based array positions rather than invented provider
IDs. The console prompt requests the same four output fields; it is still generated
text without the application's deterministic validator. Its fixed prompt graph is
**not a production runtime registry target**. Use runtime graphs for the application.

From a repository checkout containing the reviewed commit:

```bash
python3 infra/bedrock/cloudshell_summary.py
```

For a small CloudShell download, fetch these three files from the **same reviewed
commit** into a new directory: `infra/bedrock/cloudshell_flows.py`,
`infra/bedrock/cloudshell_summary.py`, and `backend/app/assistant/summary_policy.py`.
Verify the supplied SHA-256 hashes, then run `python3 cloudshell_summary.py`.
The following command pins the tested implementation commit and verifies all three files. The script prints the new Flow's
console URL; testing the old Flow continues to use the old prompt.

```bash
(
set -e
THREADLY_SUMMARY_DIR="$(mktemp -d)"
cd "$THREADLY_SUMMARY_DIR"
THREADLY_RELEASE='53c7e84f70386d35b313d0fce1dd27da0e873249'
THREADLY_RAW="https://raw.githubusercontent.com/bhowmikdham/Threadly/$THREADLY_RELEASE"
curl -fsSL "$THREADLY_RAW/infra/bedrock/cloudshell_flows.py" -o cloudshell_flows.py
curl -fsSL "$THREADLY_RAW/infra/bedrock/cloudshell_summary.py" -o cloudshell_summary.py
curl -fsSL "$THREADLY_RAW/backend/app/assistant/summary_policy.py" -o summary_policy.py
sha256sum -c - <<'SHA256'
1d2690bc50c9eabda44fc9e09a70189bb5bd9752c2241ef7162bf32b8e514a95  cloudshell_flows.py
0981de6a0bc8ac3ae69e4eb9fc45e126a8db7c546f254eaf5c60bad661fb7836  cloudshell_summary.py
2aa4a3fab38a9dcc5af4580773121de562f96b74bd2844bff605271d99fc25b3  summary_policy.py
SHA256
python3 cloudshell_summary.py
)
```

Example for the NEW Flow's input box:

```json
{
  "user_request": "Summarise this thread",
  "messages": [
    {
      "sender_name": "Club coordinator",
      "body": "Hi Jordan, please check last year's outstanding payment. Forwarded supplier message: Cedar Catering reports invoice INV-2047 remains unpaid. Please confirm its payment status before we proceed with the new order. We are happy to issue a tax invoice to the Student Activities Club, with payment after internal processing."
    }
  ]
}
```

Expected characteristics: blocker first, one concise next step, no unsupported
amount/owner/deadline, no invented message IDs, no duplicate questions, and no
coverage/assumption report inside the overview. Empty arrays are appropriate.
Keep returned output with the exact prompt/Flow release when reviewing quality.
No AWS permissions are broadened to fix language quality; role permissions remain
scoped to the selected inference profile and Australian destination models.

## Evaluation and frontend handoff

`backend/tests/fixtures/summary_quality_v1.json` has six synthetic cases: forwarded
invoice blocker, later payment confirmation, FYI without action, one material
unanswered question, quoted-source instruction injection and conflicting amounts.
Each includes a human reference and a semantic review rubric. Tests exercise
contracts, source scope, release compatibility and failure publication, not the
ability of a live model to produce the reference output.

For an explicitly authorized live replay against a **published runtime Flow**:

```bash
PYTHONPATH=backend python -m app.workflows.evaluate_summary \
  --manifest candidate-registry.json \
  --fixtures backend/tests/fixtures/summary_quality_v1.json \
  --output private-summary-quality-report.json --invoke
```

This makes six billed calls for the checked-in fixture set (maximum ten cases).
It records fixture/contract hashes, model/Flow provenance, observed output, contract
results and rubrics. Human factual/relevance review always starts `pending`;
`production_approved` stays false. Review every case, record pass/fail and the exact
reason, and retain older results for comparison. Do not present a local fake replay
as a live quality evaluation, or a successful schema check as factual accuracy.

The frontend should show `artifact.content.overview` as the primary summary, then
nonempty action/decision/question sections only when useful. Render backend coverage
and evidence separately from the summary text. Do not show raw JSON, empty headings,
model provenance or escaped newlines as the primary user experience. Copy/Insert
must be deliberately scoped to the user-facing content, and never imply sending.
No frontend implementation is included in this backend repository slice.

Local verification: run the backend suite with a required disposable PostgreSQL
instance, Ruff, `python -m unittest discover -s infra/bedrock/tests`, both documentation
validators and cfn-lint on the render-only summary template. See the checkpoint for
actual results. Outstanding live gates are the new console replay, deployed backend
runtime verification and frontend rendering/integration.
