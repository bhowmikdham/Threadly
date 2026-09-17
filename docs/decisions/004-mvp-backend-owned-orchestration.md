# ADR 004 — Backend owns the reviewed MVP orchestration and provider reads

Status: implemented in the combined MVP integration; live acceptance pending.

The target playbook proposed Bedrock read callbacks and broader automatic planning.
Current code already owns source snapshots, versions, permissions, deterministic
Calendar slots, durable tasks, approvals and provider recovery. Duplicating those
boundaries in a Lambda/Flow callback would add another authorization and retry surface.

For the MVP, the API creates a reviewed finite proposal, the backend prefetched context
feeds generation-only published Flows (or the configured native model), and the worker
executes known dependency templates. PostgreSQL owns human waits and recovery. There
is no Lambda callback and no unrestricted agent tool executor. Operation mappings and
supported combinations are explicit in `docs/mvp-workflow-map.md` and runtime schemas.
This is a deliberate narrowing of B17's proposed read-bridge design, not evidence that
the original callback acceptance tests passed.

Unsupported compound instructions do not run a supported subset. Missing fields
require a corrected full proposal. Automatic classifier-to-executor routing and
in-place free-text graph editing wait for command-domain quality evidence. The supplied
email-content classifier is not silently reused as a command classifier.

Benefits: one ownership fence, auditable exact previews, bounded retry semantics,
compatible historical releases, and a useful API-level MVP without frontend waits.
Costs: only installed graphs; more explicit review; exact quote validation can reject
masked model output; no arbitrary agent loop. Review these constraints after the pilot,
not by silently adding tool authority to prompts.
