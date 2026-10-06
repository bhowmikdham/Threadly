# Calendar-only public eligibility

## Behavior and release boundary

Base: `0e4359851c9bc90ef607b034bc91a0c310acbc6e`, the verified running backend
release when this correction was prepared. Its voice route is unchanged. No frontend,
prompt, schema, migration, Google credential or stored grant change is required.

The current extension already displays **Enable event creation** when
`calendar_write.enabled` is true and its Google grant is not ready. Under the existing
pilot policy, other accounts receive a disabled capability and the button is hidden.

This change adds `CALENDAR_PUBLIC_ROLLOUT_ENABLED=false` by default. Explicitly enabling
it makes Calendar eligibility available to current and future signed-in accounts.
Capabilities, authenticated reconnect, Calendar job selection, dispatch checks and
operational status agree on this policy. Gmail retains its existing pilot membership
and execution flag. Requests combining Calendar consent with unauthorized Gmail
sending still fail; a Calendar rollout never grants Gmail scope eligibility.

The Calendar write kill switch, actual recorded Google grants, account/session binding,
editable Calendar ACL, exact payload/hash approval and chat-scoped Ask/Always rules
remain in force. Ask remains the default for existing and newly created accounts.
No consent screen or real provider write is invoked by this change or its tests.

## Verification

Focused integration: 113 passed, zero failures/skips, one dependency deprecation
warning. This covers Calendar creation and actions, OAuth boundaries, revocable
sessions, capabilities and operational controls. The final new-account/Ask assertions
were also rerun: 10 passed, zero failures/skips.

Deployment-control suite: 32 passed. It rejects public Calendar without reconciliation,
rejects Gmail enablement without its pilot list, accepts Calendar-only public
configuration, and verifies that the public switch alone does not enable dispatch.
The Google setup helper resets the new public switch to false with its existing
write-disable behavior.

Ruff, diff checks, the implementation-plan validator and versioned MVP prompt asset
verification passed. The full backend suite and exact-head CI must also pass before
rollout; their final results are recorded on the draft PR.

All DB tests use a disposable local PostgreSQL instance and synthetic accounts.
Provider calls use mocks. The worker regression uses a transport wrapper that does
not trigger the historical MockTransport pilot bypass, so production eligibility
selection and rechecks are exercised. It verifies wrong-hash/foreign approval denial,
no claim before approval, approved dispatch, and revocation after claim before HTTP.

## Activation and rollback decision

Code review or code-release approval alone does not authorize broadening eligibility.
Obtain the explicit audience decision for all current and future signed-in users and
the scoped deployment/configuration approval before activating the switch.

At execution time, acquire the existing deployment lock and freshly verify container
images, Compose inputs and service revisions. The previously observed API-specific
marker was stale; container facts and the current stack release must agree with the
intended base. Preserve newer voice changes if production advances again.

Deploy the reviewed code consistently to the API and backend workers, retaining the
existing environment and changing only the separately approved Calendar rollout
control. Do not alter `WRITE_PILOT_USER_IDS`, Gmail flags, credentials, Google consent,
website/download files or database records. No migration is introduced by this patch.

After activation, read-only capability checks should show eligible accounts without
write grants as `enabled=true`, `ready=false`, `scope_missing` or `scope_unknown`.
Their existing extension can then offer explicit Google consent. Already-ready
accounts correctly have no enable-consent button. A screenshot from an already-ready
account still requires checking that client's account and backend origin; broadening
eligibility is not proof that every client-specific symptom is resolved.

Rollback the audience by setting the new switch false consistently in API/workers.
This restores the legacy pilot without revoking Google grants or changing Gmail.
Disable `CALENDAR_WRITES_ENABLED` as well to stop new pilot dispatch. Preserve recovery
reads for uncertain outcomes. Inspect queued approvals before any later re-enable;
they remain subject to the existing expiry, version and exact-authorization checks.
