# Offline correction of the new-email repair loop

Runtime/test head: `7fb055f409c46479bc1cd1194aa870ff93ccf187`.
Prompt/tool snapshot: `contextual-conversation-1.8.9+chat-context.5.json`.
This is an unpublished offline correction; no `.5` live model calls, provider
writes, budget resets or deployment were run. See the mechanical receipt for the
full-suite results and source/log hashes.

## Proven cause and boundary

The `.4` [resumption](second-resumption-results.md) recorded six completed model
responses for “Another email for Casey, please.” (cumulative calls 10–15). At
2026-10-07 13:35:50 UTC, request `143380f8-7d33-46aa-a8e8-7e5307a1e00f`
explicitly selected a new operation (`continue_previous:false`) but included the
stale Alex goal ID. Schema validation rejected the combination. Generic repair
then instructed the model to set continuation true, contradicting the new-email
request. Runtime correctly rejected that operation, but repeated generic repairs
consumed the six-call case cap and returned 503. Alex remained unchanged; Casey
was never created. This was an application contract/repair failure, not a failed
provider response. The evidence does not establish how every paraphrase behaves.

## Implemented correction

1. The model sees separate `start_email_draft` and `continue_email_draft` tools.
   Starting accepts no existing goal ID or continuation flag. Continuing requires
   an owned goal ID and exact current USER request. Both retain provenance checks.
2. The legacy `prepare_email_draft` dispatcher remains for compatibility and exact
   replay, but is hidden from model discovery. Explicit boolean false discards a
   stale ID before validation; it never reads or mutates the referenced goal.
   New fields must still come from current USER text or verified USER citations.
   An ID with an omitted operation returns `email_goal_operation_required`.
3. Repair instructions identify the failed reason and preserve the operation.
   Unknown/invalid owned goals require goal lookup; missing request provenance
   requires the current turn; source-based and saved-artifact work use their
   existing tools. New work is never forced to continue because old work exists.
4. Two occurrences of one email-tool error, or three total email-tool errors,
   end with the existing `email_draft_not_prepared` response. No new diagnostic
   or retry UI is added. Validated details and unrelated goals remain retained.
5. Explicit new-email rejection runs before selecting a retained goal. Valid
   bound wording revisions such as “Write a shorter version of this email” no
   longer trip the broad legacy compose heuristic. Recipient correction stays on
   the explicitly selected goal and does not inherit the old recipient's address.

The older synthetic evaluator also recognizes both new tools. Its single-goal
fixture is not ownership evidence; real-service PostgreSQL tests cover ownership
and independent goal state. No evaluator CLI or paid provider was invoked.

## Offline evidence

Before the correction, three regression assertions failed: the exact six-call
replay, the operation schema and the repeated-error bound. The same recorded first
response now produces Casey's missing-purpose clarification in one scripted call,
with a fresh goal ID and Alex's stored payload hash unchanged.

Additional tests vary recipient and wording, reject stale IDs on the new tool,
repair a missing continuation ID without allocating new work, revise recipient
and draft wording on the same goal, bound repeated unknown IDs, and bound three
distinct invalid operations. The focused final run passed 57 checks. Earlier
focused runs exposed an old repair-string assertion and the wording-revision
heuristic; both are corrected. Full backend: **2,300 passed, no skips**, one
Starlette deprecation warning, 291.95 seconds. Results and log hashes are in the receipt.
Scripted outputs verify deterministic behavior, not live model semantic quality.

Frontend PR126 is integrated at `277be033ee5d7276517adde1ab9acd1886810602`;
254 unit tests, TypeScript, both builds, 50 local browser tests and the separate
public-origin browser test passed on that head. PR127 was subsequently authorized
and integrated at `f4601ff85461283ffe535a847450a0f1427ffd58`; see the receipt for
its focused verification. PR125 remains open/unmerged, and PR128 closed/unmerged;
both are excluded. PR126 files and released VoiceOrb remain byte-for-byte unchanged.

## Narrow next live gate — proposal only, not executed or approved

Both existing allowances are exhausted (18/18 each); unspent dollars grant no
additional calls. Any further diagnostic needs a separate explicit allowance and
must preserve both existing ledgers. Do not reset or reuse either budget identity.

Proposed scope is the changed email-operation contract alone: seed Alex's pending
draft, issue the original new-Casey request, then finish Alex and Casey in separate
turns. Require distinct IDs, unchanged non-target payloads, correct recipient and
purpose, useful draft text, bounded tool counts, and zero provider actions. If the
original path fits within the cap, spend remaining calls on one independently
worded new/continue transition. Stop immediately on a semantic regression.

Suggested maximum: six paid attempts, 32,000 input / 1,800 output tokens per
attempt, no SDK retries, 15-minute safety window, and USD 0.30 including a 10% GST
allowance. At the previously verified Sydney rates this reserves USD 0.29766;
fresh pricing/CountTokens preflight is mandatory before any separately approved
run. Keep the same model and pin the final code/prompt/tool hashes. This proposal
does not authorize those calls. If six attempts cannot finish the three turns,
report incomplete coverage rather than adding calls.

Saved-artifact restoration and initial Calendar preview have live evidence only
on `.4`. Calendar revision and closing were not tested during that resumption;
they remain outside this narrow proposed email gate and remain release gates.
Migration `h071026e9043` still requires coordinated API/worker rollout; no release
approval is implied by passing the offline checks.
