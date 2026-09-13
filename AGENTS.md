# Threadly implementation context

Read `docs/implementation-playbook/README.md` for the target architecture and
`docs/implementation-playbook/09-coding-agent-guide.md` before implementing a
work package. Tasks and dependencies are in
`docs/implementation-playbook/backlog.json`.

The product targets individual professionals and small teams. User intents are
summarise, plan/schedule, reply, compose and bounded other assistance. The requested
direction is Bedrock with visual Flows and backend-owned context, approvals and
external execution.

The playbook is a target specification, not evidence that features exist. Inspect
the actual code and current `docs/api-contract.md`, `docs/data-model.md` and
`docs/architecture.md`; update runtime docs with implementation changes. Supersede
the historical inference ADR through the migration task. Follow a user's scoped
request rather than implementing the entire backlog by default.

Preserve unrelated changes. Use existing FastAPI error envelopes, lazy heavy SDK
imports, httpx Google adapters and Alembic migrations. Keep user ownership checks,
deterministic facts and exact-payload approval in backend code. Reconcile uncertain
Gmail/Calendar writes before retrying. Never confuse producing a draft, inserting
text and sending mail.

Record actual checks and skipped dependencies. Model/prompt/flow changes require
replayable evaluation evidence and versioned assets. A task is verified only when
its acceptance criteria are supported by results. Routine implementation choices
within the user's authorized scope do not require repeated confirmation.
