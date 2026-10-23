# Readable Gmail inbox — 0.2.5

Screenshot evidence: `libfile_badeb4e95f4c81919ae410839b1c3d2f` and
`libfile_6ab78689a16881919401923426ef49c3`, inspected directly. They show
three urgency chevrons, reply-needed arrow/dash, category icons and a words toggle.
The current 0.2.4 frontend already ships the classifier integration (PR126/132).
This release completes its display controls; it does not replace the classifier.

Settings now includes an explicitly labelled synthetic preview, Show marks in
Gmail, and Rather have words than icons. Existing users keep their current icon
marks. Display choices persist in this browser and update open Gmail tabs.
Words appear beside the reply/category icons. Reserved slots keep the subject
position unchanged between modes. Hover, focus, tap, Enter and Space reveal the
mark meaning; Escape, outside interaction, scrolling and navigation dismiss it.
Turning marks off hides them immediately and stops new classification calls,
including requests queued in the background. Already-running read requests can
finish; late results cannot restore hidden marks. Turning them back on reuses
unexpired results. New-message IDs invalidate stale row marks.

Only the two display booleans cross the new background/content bridge. Protected
storage remains TRUSTED_CONTEXTS. No credentials, scopes, provider permissions,
model/prompt/flow, request budgets, mailbox labels, mail order or mail contents
are changed. No archive, delete, unsubscribe, send or Gmail write is added.
Uncertain/failed classifications stay blank. Existing account binding, bounded
visible-row scheduling and shared Retry-After cooldowns remain in force.

The concept says six categories. The existing, authoritative v1 backend supports
seven (Finance, HR, IT, Legal, Meeting, Other, Projects). This release retains
those values and does not claim a new six-category model. If exact concept taxonomy
is desired, the product question is: **Which six categories should replace the
current seven, and how should existing IT/Projects values map?** That schema/model
change requires its own implementation and replayable evaluation.

## Verification

Results are recorded after the final checks below. Browser fixtures use synthetic
accounts/mail and fake Google/backend responses. They do not establish production
model accuracy or real Google consent.

Release follows frontend PR → CI → merge → exact merged-head CI → existing website
ZIP publisher. Verify the public ZIP's checksum against that workflow's feed.
Unpacked users must download/extract, reload the extension and reload Gmail.
The website download does not automatically update installed developer extensions.

Current live baseline inspected: public 0.2.4 download matches frontend
`e0fbc65b0fce6f98a29503af022586265dd3d0d3`; public API health/readiness pass.
Current AWS capacity and host state could not be read: both existing AWS profiles
reported expired sessions. No credentials or host settings were changed. This
frontend-only release preserves existing classification capacity/configuration.

Local checks: TypeScript passed; 260 unit tests passed; 29 updater tests passed;
full localhost packaged Chromium suite 56 passed, with the separate public-origin
case deliberately skipped for its own HTTPS build. The final preview-only CSS
refinement is rechecked with the focused settings/browser case. Public-origin
smoke also covers the preference bridge and persistence against synthetic Gmail,
with no authenticated account. Formatting and whitespace checks pass.

The release smoke supports `THREADLY_E2E_EXTENSION_PATH=/absolute/unpacked/path`
so the same public-origin check can load the downloaded website ZIP after checksum
verification, instead of trusting the local build as deployment evidence.
