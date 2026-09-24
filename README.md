# Threadly browser extension

A conversational Gmail side panel connected to Threadly’s backend. This extension
release depends on the contextual conversation API in backend PR #49
(`codex/contextual-conversation`, based on `codex/assistant-intent-routing`). Deploy
that reviewed backend release before testing this extension against EC2. The backend
and extension branches have separate layouts; do not combine their trees to run them.

## Install locally

Use Node 22.12+ and npm (the checked-in `package-lock.json` is authoritative).

```sh
npm ci
npm run typecheck
npm test
npm run build
```

In Microsoft Edge, open `edge://extensions` (Chrome: `chrome://extensions`),
enable Developer mode, choose **Load
unpacked**, and select `build/chrome-mv3-prod`. Reload any existing Gmail tabs.
The committed public manifest key keeps the unpacked extension ID stable:
`ffmlcgieiefcjkjkebfhfkglehgljbhk`.

Open Threadly from the browser’s extensions menu or the Gmail hover widget. Settings
lets you choose your backend. The default is `http://127.0.0.1:8000`.

## EC2 development connection

Run this on your laptop with AWS CLI and Session Manager plugin installed and the
`threadly` AWS profile signed in. Leave the terminal open:

```sh
aws ssm start-session --profile threadly --region ap-southeast-2 \
  --target i-09a783f8a5b22df7f \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8000"],"localPortNumber":["8000"]}'
```

This reaches the existing loopback-only EC2 API. It does not expose port 8000 to
the internet. The instance must be running. For a deployed HTTPS API, enter its
origin in Settings and grant only that server’s requested host permission.
Changing servers signs you out.

If Sign in says the backend cannot be reached, check that the tunnel terminal is
still open and the EC2 instance is running. Open `http://127.0.0.1:8000/readyz`
locally to check the connection; it should return `"status":"ok"`. An expired AWS
login must be renewed before starting a new tunnel. Retry Sign in after restoring
the connection. The extension cannot create the AWS tunnel itself.

## Google sign-in configuration

Use the backend’s existing Google **Web application** OAuth client. In Google
Cloud → Google Auth Platform → Clients → the client → **Authorized redirect
URIs**, add exactly:

```text
https://ffmlcgieiefcjkjkebfhfkglehgljbhk.chromiumapp.org/oauth/callback
```

Keep the existing laptop callback if still used. Add this same extension URL to
the backend’s comma-separated `GOOGLE_REDIRECT_URI_ALLOWLIST` and recreate the API
and workers with the protected environment file. The backend client ID and
secret stay on EC2; no Google secret, Bedrock key, Gemini key, or public AI API key
belongs in the extension. Test accounts must be included in Google’s test-user
list while the OAuth app is in Testing.

Click **Sign in with Google**. Connect Calendar separately in Settings, load
calendars, select the calendars to check, and explicitly save working hours,
timezone, buffers, notice and duration. Sending and booking connections are
optional and remain subject to backend pilot controls and exact approval.

## What is connected

- Chat-driven Gmail discovery: ask “Show me all the GYG emails”; view up to five
  glass email cards per page, with visible dates and Show more. The backend searches
  Gmail on demand within bounded coverage. No search form or mailbox import.
- Normal greetings, compact composer/context chip, copy/time on responses and a
  conversation drawer. Literal flight routes get an animated itinerary card, with
  reduced-motion support and no claim of live flight tracking.
- Select an open Gmail thread or a search result; choose source messages and a
  specific reply/rewrite target from the context chip. Opening a new conversation
  automatically attaches the open Gmail thread once; navigating Gmail does not
  silently replace the pinned source. Browser message bodies are not imported.
- Summary, grounded questions, reply and new-email drafts, and selected-message
  rewriting through durable backend tasks (context chip → choose message →
  Rewrite selected message). Ordinary chat turns use
  `POST /assistant/conversation-turns`; Bedrock can answer directly, retrieve bounded
  email evidence or hand work to the existing specialist workflows. The explicit
  context-chip rewrite remains a typed backend task.
- Reviewed multi-step plans and scheduling; per-step progress, cancellation,
  typed clarification, task history and reconnectable result status. The current
  conversation restores from backend history in the same browser session; search
  cards are refreshed by asking again.
- Editable draft revisions, exact outgoing preview, explicit approval, and
  uncertain-send recovery. **Copy**, **Insert body**, and **Send** are separate.
- Calendar preferences, slot selection with a fresh recheck, exact event preview,
  explicit approval and event status/recovery.
- Browser dictation puts text into the input for review; it never submits by
  itself. Availability depends on the browser and microphone permission.

No mailbox sync, automatic send, automatic booking, direct Google API calls from
the side panel, public model keys, or fabricated fallback classifications remain.
The old subject-only Gemini inbox badges are removed: the current backend has no
corresponding classification API. Backend voice endpoints are not claimed as
implemented; dictation uses the browser’s own capability.

The new chat endpoint is disabled by default on EC2. The reviewed backend release
must be deployed with its migration, the configured Sydney Bedrock inference profile,
and `CONVERSATION_ENABLED=true`. Enable
`BEDROCK_MAIL_PROCESSING_ACKNOWLEDGED=true` only after reviewing the model's
content boundary and account invocation-logging settings. Keep external email and
Calendar writes disabled for the initial read/generation test. Without the chat
feature gate, normal messages return `conversation_disabled` rather than silently
falling back to the old browser-side rules.

## Tests and handoff

```sh
npm test                       # trust boundaries, forms and exact approval
npm run build
npx playwright install chromium
npm run test:e2e                # real packaged extension + deterministic local API
```

For the opt-in **live** check, first perform the existing laptop OAuth login and
open the tunnel. The helper reads your private `~/.threadly-staging/session.json`
without printing it, then uses an isolated, deleted-after-run Chrome profile:

```sh
THREADLY_LIVE_TEST=1 node scripts/live-smoke.mjs
```

This fetches bounded GYG search results and selected thread content, invokes the
configured model for summary/question/drafts, and reads Calendar metadata. It
never sends email or creates events. Set `THREADLY_MAIL_QUERY` to another search
term if needed. It tests existing authenticated sessions in an isolated Chromium
profile, **not** the interactive Google consent window or Edge-specific UI.

See [conversation design and boundaries](docs/conversation-experience.md) and
[integration handoff](docs/frontend-integration.md) for endpoint mappings,
recovery behavior, evidence and remaining external setup.

See [inbox chat behavior and checks](docs/inbox-chat.md) for the new discovery APIs and limits.
