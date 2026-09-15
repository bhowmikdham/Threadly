# 12 · Work package cards

Generated from [backlog.json](backlog.json). Edit the JSON and regenerate with `python3 docs/implementation-playbook/tools/build_task_cards.py` so human and coding-agent instructions stay aligned. Status is copied from the backlog; implementation completion requires the recorded evidence.

## T01 · Agree contracts and supersede inference architecture

**Owner:** Backend · **Status:** in_progress · **Release:** Initial release

**Depends on:** No implementation dependency; review the playbook.

**Implementation targets (may not exist yet):** `docs/api-contract.md`, `docs/data-model.md`, `docs/architecture.md`, `docs/decisions/`, `backend/app/schemas/`, `ml/README.md`

### Deliverables

- Runtime contract definitions derived from reviewed examples
- New ADR for Bedrock and backend-owned workflow state
- Document artifact/action/task semantics and agreed review rules

### Acceptance criteria

- Five intents, continuation, source context and approval examples reviewed across teams
- Current versus proposed paths and API compatibility documented
- No production behavior claimed before implementation

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

AI and frontend receive versioned schemas and fixture inputs.

## T02 · Repair Gmail fidelity and preserve reply metadata

**Owner:** Backend · **Status:** in_review · **Release:** Initial release

**Depends on:** T01

**Implementation targets (may not exist yet):** `backend/app/sync/`, `backend/app/db/repositories.py`, `backend/app/db/models.py`, `backend/alembic/versions/`

### Deliverables

- Monotonic thread version and stable message order
- Backfill starting-cursor plus replay strategy
- Parsed sender equality and RFC reply metadata

### Acceptance criteria

- Reverse/equal-time ingestion cannot regress newest context
- Concurrent arrival during initial sync is captured
- Alias/substring spoof and missing Date fixtures handled
- Migration preserves current message rows

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Context and sender services can rely on stable source identifiers.

## T03 · Google capabilities and AWS connectivity foundation

**Owner:** Backend · **Status:** planned · **Release:** Initial release

**Depends on:** T01

**Implementation targets (may not exist yet):** `backend/app/auth/`, `backend/app/config.py`, `.env.example`, `infra/bedrock/`, `infra/deploy/`

### Deliverables

- Incremental scopes and actual-grant capability tracking
- Revocation/transient-error distinction and safe token refresh
- Verified model/role/region access and authenticated Lambda-to-backend connectivity

### Acceptance criteria

- Existing Gmail users add Calendar without losing refresh token
- Wrong-account linking and denied scopes fail safely
- No secret enters extension or flow prompt
- Deployment-role and runtime-role permissions tested

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

AI gets verified model/profile choices; tool bridge gets approved connectivity.

## T04 · Bedrock model provider and typed inference harness

**Owner:** AI · **Status:** in_progress · **Release:** Initial release

**Depends on:** T01, T03

**Implementation targets (may not exist yet):** `backend/app/model_client/`, `backend/app/config.py`, `backend/pyproject.toml`, `ml/prompts/`, `ml/evals/`

### Deliverables

- Bedrock adapter for text/typed inference with fake provider
- Task-oriented model configuration and bounded retries
- Router/generation model benchmark and PII/reference handling

### Acceptance criteria

- Iteration-time errors handled without splicing providers
- Malformed/schema-incompatible responses are bounded failures
- No silent region/provider fallback
- Model selection report includes latency, cost inputs and limitations

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Router and workflow teams receive a tested inference interface.

## T05 · Durable task, artifact, action and job infrastructure

**Owner:** Backend · **Status:** in_progress · **Release:** Initial release

**Depends on:** T01

**Implementation targets (may not exist yet):** `backend/app/jobs/`, `backend/app/actions/`, `backend/app/db/`, `backend/app/api/routes/assistant.py`, `backend/alembic/versions/`, `backend/app/assistant/`

### Deliverables

- Generic owner-scoped task/artifact/action records
- Transactional job enqueue, leases and fencing
- API snapshots and replayable task events
- Exact approval binding and cancellation semantics

### Acceptance criteria

- Real PostgreSQL races produce one valid action claim
- Duplicate request key with different body returns conflict
- Restart resumes read stages and reconciles in-flight writes first
- Artifact-only completion differs from sent/booked objective
- Cross-user IDs never disclose or mutate records

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

All workflows can persist progress and pause for users.

## T06 · Intent routing and contextual reference resolution

**Owner:** AI · **Status:** in_progress · **Release:** Initial release

**Depends on:** T02, T04, T05

**Implementation targets (may not exist yet):** `backend/app/planner/`, `backend/app/context/`, `backend/app/orchestrator/intents.py`, `ml/prompts/`, `ml/evals/`

### Deliverables

- Five-intent router with explicit-action fast path
- Bounded multi-step plan validator
- Snapshot-bound this/third/that/second-option resolver

### Acceptance criteria

- Explicit actions deterministic
- Third thread versus third message ambiguity asks correct question
- Task continuation wins over global rerouting
- Calendar-required reply uses scheduling capability
- Unknown/unsupported/missing-permission results are distinct

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Dispatcher receives validated operations and bound context.

## T07 · Flow registry, read-tool bridge and versioned promotion

**Owner:** Backend · **Status:** in_progress · **Release:** Initial release

**Depends on:** T03, T04, T05

**Implementation targets (may not exist yet):** `backend/app/orchestrator/registry.py`, `backend/app/model_client/flow_client.py`, `backend/app/api/routes/internal_tools.py`, `infra/bedrock/`, `infra/lambda/`

### Deliverables

- InvokeFlow adapter and registry
- Named read adapters with task-bound grants
- Exported test flows, immutable versions and release aliases

### Acceptance criteria

- Fake and live adapter contracts match
- No write tool is exposed in proposal graphs
- Model cannot change user context or tool grant
- Pinned release survives deployment of a new version
- Node error branches become typed app outcomes

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

AI can author tested flow graphs against stable tool contracts.

## T08 · Source-linked summary vertical slice

**Owner:** AI · **Status:** in_progress · **Release:** Initial release

**Depends on:** T02, T05, T07

**Implementation targets (may not exist yet):** `backend/app/orchestrator/`, `backend/app/context/`, `backend/app/api/routes/summary.py`, `ml/prompts/`, `ml/evals/`, `backend/app/assistant/`

### Deliverables

- Structured summary with coverage/evidence
- Release-aware cache key
- Long-thread chunking with original evidence retained

### Acceptance criteria

- New message or prompt release invalidates cache
- Collapsed content fetched or marked unavailable
- Material claims have valid source IDs and reviewed support
- Selection scope and full-thread scope distinguishable

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Frontend receives first fully functioning read-only result.

## T09 · Evidence retrieval and bounded Other handlers

**Owner:** Backend · **Status:** planned · **Release:** Initial release

**Depends on:** T02, T04, T05

**Implementation targets (may not exist yet):** `backend/app/extractor/`, `backend/app/db/`, `backend/app/api/routes/entities.py`, `backend/app/retrieval/`

### Deliverables

- Entity/commitment extraction and provenance
- Bounded mail search with indexing status
- Help and selection-transform handlers

### Acceptance criteria

- Exact entity lookup avoids unnecessary generation
- Empty index does not imply absent email
- All retrieved evidence owner-scoped
- Unknown operation cannot run arbitrary SQL/tool
- Source deletion/revocation reported

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Reply, Compose and Plan can use grounded facts and commitments.

## T10 · Shared draft artifacts, recipients and revisions

**Owner:** Backend · **Status:** in_progress · **Release:** Initial release

**Depends on:** T01, T05, T09

**Implementation targets (may not exist yet):** `backend/app/drafting/`, `backend/app/schemas/draft.py`, `backend/app/db/models.py`, `backend/app/api/routes/draft.py`

### Deliverables

- Draft revision model with subject/To/Cc/Bcc/body
- Recipient resolution and missing-fact validator
- Artifact-to-action proposal builder

### Acceptance criteria

- No guessed recipient addresses or inferred Bcc
- Edits invalidate previous approval
- Missing attachment/placeholder blocks send
- Existing draft rows have a coherent migration/link strategy

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Reply and Compose share the same review and sender contracts.

## T11 · Reply workflow

**Owner:** AI · **Status:** in_progress · **Release:** Initial release

**Depends on:** T06, T07, T10

**Implementation targets (may not exist yet):** `backend/app/drafting/service.py`, `ml/prompts/`, `ml/evals/`, `infra/bedrock/flows/`

### Deliverables

- Grounded reply flow and prompts
- Reply/reply-all and optional shared capabilities
- Revision handling and current-thread recheck policy

### Acceptance criteria

- Scheduling-dependent reply fetches real availability
- No unsupported promises or attachment claims
- Correct target and recipients remain visible
- Follow-up rewrite edits current artifact without wrong-task routing

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Reviewable thread reply integrates with shared sender.

## T12 · Approved Gmail sender and uncertain-outcome reconciliation

**Owner:** Backend · **Status:** planned · **Release:** Initial release

**Depends on:** T02, T03, T05, T10

**Implementation targets (may not exist yet):** `backend/app/actions/`, `backend/app/sync/gmail.py`, `backend/app/drafting/mime.py`, `backend/app/api/routes/draft.py`

### Deliverables

- Exact reviewed MIME send
- RFC Message-ID and provider result persistence
- Unknown-send reconciliation and duplicate/cancel handling

### Acceptance criteria

- Reply thread headers correct; new mail has no inherited thread
- Double approval/redelivery cannot blindly resend
- Timeout after provider acceptance enters outcome_unknown
- Old send route cannot bypass shared approvals
- Reported sent status backed by provider evidence

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Reply/Compose can complete approved actions with recovery evidence.

## T13 · Compose workflow and missing-information handling

**Owner:** AI · **Status:** in_progress · **Release:** Initial release

**Depends on:** T06, T07, T10

**Implementation targets (may not exist yet):** `backend/app/drafting/`, `ml/prompts/`, `ml/evals/`, `infra/bedrock/flows/`

### Deliverables

- New-email purpose/recipient clarification
- Subject/body generation using authorized facts
- New-message draft output

### Acceptance criteria

- Ambiguous names ask for recipient choice
- Unknown project facts are not invented
- Background thread evidence does not make it a reply
- Unresolved placeholders cannot be approved for send

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Frontend receives new-email artifacts using shared editor/executor.

## T14 · Non-calendar action planning

**Owner:** AI · **Status:** planned · **Release:** Initial release

**Depends on:** T06, T07, T09

**Implementation targets (may not exist yet):** `backend/app/planning/`, `backend/app/db/`, `ml/prompts/`, `ml/evals/`

### Deliverables

- Editable plan artifact with dependencies/evidence
- Explicit/inferred/confirmed commitment distinctions
- Internal accept-plan operation and linked draft path

### Acceptance criteria

- Dependency cycles and impossible dates rejected
- No invented owner/deadline treated as confirmed
- User chooses which commitments enter a reply
- Plan edits invalidate dependent claims/actions

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Plan/schedule category supports actual work planning independently of Calendar.

## T15 · Calendar preferences and deterministic availability

**Owner:** Backend · **Status:** planned · **Release:** Initial release

**Depends on:** T03, T05

**Implementation targets (may not exist yet):** `backend/app/calendar/`, `backend/app/api/routes/calendar.py`, `backend/app/db/`, `backend/alembic/versions/`

### Deliverables

- Calendar selection/preferences/versioning
- Freebusy REST client and pure slot engine
- Time-zone/date assumption records

### Acceptance criteria

- DST gap/fold, buffers, all-day busy and overlapping calendars pass fixtures
- Per-calendar error means unknown
- Fewer-than-three result supported without invented slots
- Permission/ACL checks enforced
- No sender-availability claim without access

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Scheduling flow gets verified slot IDs and availability evidence.

## T16 · Scheduling negotiation and approved booking

**Owner:** Backend · **Status:** planned · **Release:** Initial release

**Depends on:** T06, T07, T12, T15

**Implementation targets (may not exist yet):** `backend/app/planning/`, `backend/app/calendar/`, `backend/app/actions/`, `ml/prompts/`, `infra/bedrock/flows/`

### Deliverables

- Check-time/suggest-slots workflow
- Pinned offers and delayed reply resolution
- Event preview, exact approval and stable-ID reconciliation

### Acceptance criteria

- Second-option reply resolves original offer version
- Calendar/thread changes invalidate old proposal
- Sending options does not create events
- Create/notify payload exactly matches approval
- Existing invite is linked instead of duplicated
- Event created is distinct from attendee acceptance

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

All planning/scheduling milestones ready for complete UI acceptance.

## T17 · All-intent frontend integration and contextual actions

**Owner:** Frontend · **Status:** in_progress · **Release:** Initial release

**Depends on:** T01, T05, T06

**Implementation targets (may not exist yet):** `frontend/`, `docs/api-contract.md`

### Deliverables

- Task composer/list/clarification/evidence/review components
- Snapshot/reference mapping adapter
- Copy/Insert/Send distinct UX and provider outcome display

### Acceptance criteria

- All five intents accessible through buttons and text
- Third-message versus third-thread fixtures pass
- Multiple tasks/composers retain correct targets
- Reconnect recovers current state
- Insert preserves user content or previews replacement
- No false send on Copy/Insert

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Joint E2E can exercise every artifact/action in the actual extension.

## T18 · Joint all-intent quality and recovery gate

**Owner:** AI · **Status:** in_progress · **Release:** Initial release

**Depends on:** T08, T09, T11, T12, T13, T14, T16, T17

**Implementation targets (may not exist yet):** `backend/tests/`, `ml/evals/`, `.github/workflows/`, `docs/implementation-playbook/`

### Deliverables

- Held-out evaluation and scorecard
- Real DB and provider failure-injection suite
- All-intent extension walkthrough with evidence

### Acceptance criteria

- Chapter 08 hard invariants pass
- Reported latency/quality metrics separate warm/cold and partial coverage
- Skipped DB tests fail required gate rather than masquerading as verification
- No critical wrong-recipient/unsupported-write/false-success defect remains

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Release owner gets complete evidence package and known limitations.

## T19 · Staged pilot deployment, monitoring and rollback

**Owner:** Backend · **Status:** planned · **Release:** Initial release

**Depends on:** T18

**Implementation targets (may not exist yet):** `infra/deploy/`, `infra/bedrock/`, `backend/app/config.py`, `docker-compose.yml`

### Deliverables

- Worker deployment, manifests and independent write flags
- Dashboards, budgets and unknown-action alerts
- Backup/restore and release rollback drill

### Acceptance criteria

- Read pilot followed by controlled writes passes checkpoints
- Old active tasks retain compatible release assets
- Kill switches disable writes without prompt redeploy
- Operational owner and recovery playbooks assigned

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Initial five-intent product can be enabled for the agreed audience.

## T20 · Opt-in proactive suggestions and follow-up nudges

**Owner:** Backend · **Status:** planned · **Release:** Optional follow-on

**Depends on:** T19

**Implementation targets (may not exist yet):** `backend/app/sync/`, `backend/app/jobs/`, `backend/app/suggestions/`, `frontend/`

### Deliverables

- New-mail/due-item eligibility and suppression
- Watch/poll reconciliation and quiet-hour controls

### Acceptance criteria

- Historical backfill produces no unsolicited flood
- Dismissed/replied/stale suggestions suppressed
- No autonomous send implied by detection

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Measured proactive pilot.

## T21 · Consent-based sent-mail style retrieval

**Owner:** AI · **Status:** planned · **Release:** Optional follow-on

**Depends on:** T10, T18

**Implementation targets (may not exist yet):** `backend/app/rag/`, `ml/evals/`, `frontend/`

### Deliverables

- Per-user style indexing and consent controls
- Style-only retrieval with graceful fallback

### Acceptance criteria

- Retrieved style cannot introduce facts or recipients
- Cross-user retrieval impossible
- Chroma outage preserves core drafting

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Optional personalization release.

## T22 · Voice input/output through existing task contracts

**Owner:** Backend · **Status:** planned · **Release:** Optional follow-on

**Depends on:** T04, T17

**Implementation targets (may not exist yet):** `backend/app/voice/`, `backend/app/api/routes/voice.py`, `frontend/`

### Deliverables

- STT input bound to task/snapshot
- Optional TTS with privacy controls

### Acceptance criteria

- Low-confidence names/dates require confirmation
- Transcription never directly executes a write
- Typed and voice routing behavior is equivalent

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Optional accessible input modality.

## T23 · Attachment-aware understanding and sending

**Owner:** Backend · **Status:** planned · **Release:** Optional follow-on

**Depends on:** T19

**Implementation targets (may not exist yet):** `backend/app/context/`, `backend/app/attachments/`, `backend/app/actions/`, `ml/evals/`

### Deliverables

- Supported file allowlist, size/type validation and provenance
- Attachment extraction coverage and approval binding

### Acceptance criteria

- Unsupported or unsafe file type handled explicitly
- Draft attachment claims match actual approved files
- Ownership and duplicate-upload constraints enforced

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Separately evaluated attachment capability.

## T24 · Calendar changes and richer meeting resources

**Owner:** Backend · **Status:** planned · **Release:** Optional follow-on

**Depends on:** T16, T19

**Implementation targets (may not exist yet):** `backend/app/calendar/`, `backend/app/actions/`, `ml/evals/`

### Deliverables

- Reschedule/cancel and recurrence/resource-specific policies
- New action contracts and compensating-action previews

### Acceptance criteria

- Existing event identity/organizer permissions checked
- Changes and notifications separately approved
- Recurring-event scope explicit; no silent whole-series mutation

### Evidence and handoff

- Relevant test/evaluation commands and results
- Contract/configuration changes and known limitations
- Reviewable implementation diff or versioned asset

Optional advanced scheduling release.
