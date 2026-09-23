# Inbox chat and glass result cards

This extends the conversational panel with normal greetings and natural inbox
search. Deploy backend `inbox-chat-1.0.0` before this extension. Backend contract:
`docs/inbox-chat.md` on the backend branch. No new extension permissions or runtime
animation dependency are required.

- `POST /assistant/inbox-chat`: backend owns search extraction and date resolution;
  message replies render directly, searches render cards, continue delegates to
  existing assistant requests. Clarification and refinement stay ahead of this call.
- `POST /assistant/inbox-search-page`: exact saved filters and opaque cursor;
  Show more appends/deduplicates messages, “show more” creates another chat turn.
- Card selection reads the owned thread, verifies the clicked message exists,
  and pins that message as the explicit reply/rewrite target. Summary still sees
  the selected thread. Open in Gmail uses the connected account and thread ID.
- `Find an email` now focuses a natural-language prompt instead of opening a form.
- Search cards show subject, sender/date and plain text preview. The backend returns
  at most five messages per page. Date coverage is always visible; no total inbox
  count or completeness is implied. Previous selected context changes only when
  the user explicitly chooses a card / changes context.
- Flight cards require explicit source route codes; SVG/CSS draw a short decorative
  plane arc. Reduced motion uses a static plane. Missing/unsupported itinerary
  details remain normal mail cards. Never infer live flight status.
- The composer stays compact; long source subjects ellipsize above it. The drawer
  exposes actual history and settings, without nonfunctional agent listings.

Search/greeting turns currently live in the open panel only; durable task history
continues for generated outputs. Follow-ups use an explicitly selected card;
arbitrary ordinal references (“the second one”) and open-ended memory are not
implemented. Unsupported discovery/action combinations continue to the existing
planner and remain subject to its supported operations and approval rules.

Validation: typecheck, unit trust-boundary tests, packaged Chromium tests for greeting,
five-card pagination, flight rendering, source attachment and existing workflow
continuations. `scripts/live-smoke.mjs` additionally exercises authenticated EC2
search/summary/question/draft/Calendar without external writes. Screenshots use
synthetic messages; private mailbox contents must not be committed.
