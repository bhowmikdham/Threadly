# Compact snippets and normal cursor — 0.2.6

The user's clarified request is to reduce the preview text after the subject.
Their second screenshot confirms the existing words preference displays reply
and category labels correctly; there is no missing-label migration or category
change. Source screenshots are private Library evidence only, never test data.

The shipped 0.2.5 ZIP was reproduced using synthetic cached classifications.
Both sibling and nested Gmail preview structures had no preview-width cap.
The new cap assertions failed against that exact published package. Its marks
also explicitly selected `cursor: help`, which produces the question-mark cursor.

The patch applies CSS ellipsis only to `.y2` preview snippets inside recognized
Inbox rows: at most 24ch on wide views, 16ch below 1000px and 8ch below 700px.
Snippet flex space yields before the subject. Subject nodes/content and Gmail's
native subject rules are unchanged, including native narrow-view overflow.
Original preview text remains in the DOM and accessibility tree. No email text
is rewritten, removed, copied into settings, transmitted or stored by this patch.

Left mark slots reduce from 118px to 104px, categories from 92px to 88px, retaining
fixed alignment between words/icons. Category marks stay at the right edge.
The normal cursor replaces `help`; hover/focus/tap explanations remain available.
Existing preferences are preserved. Disabling marks or leaving Inbox restores
the native preview layout. No backend, model, credentials or permissions change.

`tests/e2e/inbox-row-layout.spec.ts` uses cached synthetic labels for all seven
categories and both reply states, sibling/nested snippets and 1440/760/640px
views. It checks subject/preview text preservation, visual truncation, cursor,
words/icons alignment, native checkbox/star/open actions and disabled recovery.
It can load extracted public release bytes through THREADLY_E2E_EXTENSION_PATH.
Visual checks use synthetic screenshots only.

## Separate security-awareness request

The current backend labels and reason codes contain no security-alert signal.
High priority or IT is not sufficient evidence, and sender/subject text cannot
authenticate an email. No detector is added to this visual patch.

Proposed separate slice: an explicitly tentative, local awareness cue for a
small reviewed set of subject patterns (for example a direct security-alert or
unrecognized-sign-in notice), with marketing/newsletter exclusions. Mark it as
possible, show “Do you recognise this security alert?”, and explain that the
message has not been authenticated. Use a static accessible cue plus a restrained
finite fading glow, disable motion under prefers-reduced-motion, and permit
dismissal. Never open links, reply or perform account actions. Synthetic tests
must cover matching notices, false positives, malicious markup, narrow layout,
screen-reader text and reduced motion. This local option adds no model sharing.

If semantic detection beyond those disclosed local patterns is required, use a
separate versioned backend/schema/prompt change and replayable evaluation. That
requires deciding the detection contract before implementation; no seventh/
eighth “Security” category is invented from existing IT labels.

Local verification: TypeScript and format/whitespace checks passed; 260 unit and
29 updater tests passed; full localhost packaged browser suite 58 passed (the
HTTPS-only case ran separately). Final spacing/640px checks and the public-origin
package passed all three focused browser tests. Synthetic standard/narrow images
were visually inspected. The release still requires PR CI, publication and public
ZIP checksum/browser verification before being reported live.
