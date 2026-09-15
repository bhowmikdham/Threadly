# Concise summary correction

Current candidate: `summary-quality-1.0.2`, branch `codex/summary-grounding-contract`,
based on `760b0f5` (merged PR #17). Earlier candidate reports are retained below.
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
| `overview` | Issue/outcome/blocker first, 1–3 sentences; no minimum length, hard limit 80 |
| `decisions` | At most 3 explicit agreements; offers are not decisions |
| `actions` | At most 3 explicitly requested or committed, outstanding source actions; no inferred advice |
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
After reviewing the ten-case contract and local evidence, the following command
prepares the isolated **1.0.2 candidate**. It pins the locally tested implementation
and verifies all three files; it does not establish live model quality or enable the backend. The script prints the new Flow's
console URL; testing the old Flow continues to use the old prompt.

```bash
(
set -e
THREADLY_SUMMARY_DIR="$(mktemp -d)"
cd "$THREADLY_SUMMARY_DIR"
THREADLY_RELEASE='fa5ad150df228743ea00d1b2c46a268786f48aa5'
THREADLY_RAW="https://raw.githubusercontent.com/bhowmikdham/Threadly/$THREADLY_RELEASE"
curl -fsSL "$THREADLY_RAW/infra/bedrock/cloudshell_flows.py" -o cloudshell_flows.py
curl -fsSL "$THREADLY_RAW/infra/bedrock/cloudshell_summary.py" -o cloudshell_summary.py
curl -fsSL "$THREADLY_RAW/backend/app/assistant/summary_policy.py" -o summary_policy.py
sha256sum -c - <<'SHA256'
1d2690bc50c9eabda44fc9e09a70189bb5bd9752c2241ef7162bf32b8e514a95  cloudshell_flows.py
4cfd03f74dff5dda567eefe9f1ed329dcfe36f0cab49f0189b57710c5f2d0fae  cloudshell_summary.py
6e71ae15aa7f9e3b2f3f2e8e3aeb05cf5ff556f2ba736bddd3523ea4794c6b4b  summary_policy.py
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

`backend/tests/fixtures/summary_quality_v2.json` has ten synthetic cases, extending the preserved v1 suite: forwarded
invoice blocker, later payment confirmation, FYI without action, one material
unanswered question, quoted-source instruction injection and conflicting amounts.
The new cases add a passive delivery update, explicit work request, unaccepted offer
and answered question. Each includes a human reference, case-specific regression
constraints and a semantic review rubric. Tests exercise
contracts, source scope, release compatibility and failure publication, not the
ability of a live model to produce the reference output.

For an explicitly authorized live replay against a **published runtime Flow**:

```bash
PYTHONPATH=backend python -m app.workflows.evaluate_summary \
  --manifest candidate-registry.json \
  --fixtures backend/tests/fixtures/summary_quality_v2.json \
  --output private-summary-quality-report.json --invoke
```

This makes ten billed calls for the checked-in fixture set (maximum ten cases).
It records fixture/contract hashes, model/Flow provenance, observed output, contract
results, regression constraint failures and rubrics. Human factual/relevance review always starts `pending`;
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

## Question format correction — policy 1.0.1

A user-reported room test returned an object inside `open_questions` and restated
that question as an action. The existing backend rejects the object type. Policy
`summary-quality-1.0.1` now explicitly requires JSON strings, provides a populated
string example, and prefers one open question for an explicitly unanswered fact or
choice. Actions should describe distinct work rather than paraphrase that question.
The schema and budgets stay unchanged. Semantic paraphrase duplication still needs
human evaluation; the backend does not use unreliable text-similarity heuristics.

The original policy is retained in `summary_policy_v1.py`. Saved contract hashes
select the matching prompt, so queued 1.0.0 jobs keep their original wording; new
jobs in that release pinned 1.0.1. Current jobs pin 1.0.2; both older policies
are retained byte-for-byte and their historical contract hashes are regression-tested. Unknown policy hashes fail closed. The console launcher creates a
new content-addressed Flow and prints its prompt release. Use the new printed URL.

[Manual review record](evaluation/summary-console-review-v1.json): three satisfactory
samples (two with minor wording notes), one failure, two untested. These are pasted
outputs; Flow/trace identifiers were not supplied, so deployment identity is not
independently verified. A separate plan-shaped response remains an unresolved
configuration anomaly. None of these samples is a live pass for policy 1.0.1.

Historical 1.0.1 test instructions (superseded by the 1.0.2 gate below): rerun the room case first. Expect `actions: []` and
`open_questions: ["Is Room A or Room B booked?"]`, then rerun all six scenarios on
that same new Flow. Preserve the Flow name/release with results. No claim that
this prompt correction has passed a live model test is made before those results.


## Grounding correction — candidate 1.0.2

The user-reported 1.0.1 delivery result invented “Confirm whether this revised date
is acceptable and any downstream impacts” and an acceptance question. That output
passes the runtime JSON validator. The failure is semantic: a plain delivery
update does not request new work. The previous “next step that matters” / “clearly
necessary next step” prompt wording encouraged this behavior.

The new policy removes both instructions and the minimum word target. It defines
status updates, explicit outstanding work, actual unanswered questions, offers,
resolved requests and decisions separately. These rules apply to overview as well
as arrays. Explicit actions remain supported; simply emptying every action array
would lose legitimate user information. Advice/compound work belongs to the
[master request workflow](master-workflow.md), not an unsolicited summary step.

The runtime output schema, budgets and evidence builder remain unchanged. This is
not a universal semantic rejection filter. `workflows/summary_checks.py` adds
**fixture-specific evaluation checks**, shared by offline and live replay. They
catch the reported failure, advice hidden only in overview, missed explicit actions,
and question/action shape expectations. Different hallucinated wording or a false
claim with valid references can still require human review.

Replay the two actual user-pasted 1.0.1 outputs locally, with no AWS calls:

```bash
PYTHONPATH=backend python -m app.workflows.evaluate_summary \
  --fixtures backend/tests/fixtures/summary_quality_v2.json \
  --observations docs/evaluation/summary-console-observations-v1_0_1.json \
  --output /tmp/threadly-summary-observed-review.json
```

Expected: **nonzero exit**, 2 structurally valid cases, 1 regression pass, 1 failure,
8 cases not run. The eight include four original cases not rerun on 1.0.1 and four
new boundary cases. The report records the checked contract hash separately from
unknown offline generation identity. Do not reinterpret these as 1.0.2 results.
Repeated runs require a fresh output path; reports are never overwritten.

Before another candidate is enabled:

1. Review the ten inputs, expected behavior and rubric together; keep input and
   output records synthetic. Local tests must reject known bad observations and
   preserve positive actions. A hand-authored reference passing is not a model pass.
2. Review the versioned prompt and rendered graph. An isolated console experiment
   can then collect candidate outputs; keep its prompt/Flow/model identity and
   every result. Convert fixture bodies to the console `messages[].body` adapter
   without changing their text. Offline replay accepts objects or raw output strings.
3. Run all ten cases on the same candidate. Structural checks and fixture constraints
   must all pass, then a human checks each factual clause, attribution, relevance,
   unresolved status and absence of unsolicited work. Any prompt change restarts
   the full candidate run. `production_approved` never becomes true automatically.
4. Verify the published runtime Flow through the real backend, including its
   different graph, masking, context binding and frontend rendering, before rollout.

Current candidate 1.0.2 has **no live model results**. The two 1.0.1 observations
have no independently verified prompt hash/Flow trace. Local regression success
therefore prepares the next candidate; it does not prove live language quality.
