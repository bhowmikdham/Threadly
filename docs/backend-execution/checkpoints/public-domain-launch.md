# threadly.au public launch — in progress, 1 October 2026

User confirmed threadly.au and authorized public access/sign-in. Backend base
515628d2128a8684b075e1e842d3aec88a8327e5; frontend base
5e2f2eb460d6f05c704b2f7ea1af069142a280aa. Original root checkout preserved.

## Prepared

- Isolated backend worktree public-domain-signin, branch codex/public-domain-signin.
- User rejected the initial generated site. Replaced it with the supplied friend's
  Downloads/index.html design; original file unchanged. Cream/gold, typography,
  themes and interactive demos preserved. Release claims corrected and concepts
  labelled. Placeholder install/footer links connected to local public routes.
- Motion pinned and locally bundled. Reply-marker click and keyboard bugs fixed.
- --public-launch serves threadly.au + www redirect + api.threadly.au, verifies all
  three TLS names and disables staging autostop only after successful checks.
- Launch fails before stopping services if download/pages/contact drafts unfinished.
- Frontend public-extension-release worktree: packaged public-origin build verified;
  store-submit workflow now pins api.threadly.au (previously could ship localhost).
- Bundle and SHA metadata: primary checkout output/public-launch/.

## Evidence

- Final infrastructure regression: 29 tests passed, no skips, using the existing
  backend/.venv Python. Initial system-Python attempt failed due missing Pydantic
  and Alembic; rerun with project dependencies passed.
- Caddy 2.11.4 actual container validation passed; Compose public-launch mappings
  resolve correctly and publish only Caddy 443 plus existing loopback API 8000.
- Public extension build/typecheck passed, packaged-origin browser test 1 passed.
- Website build passed. CUA checks: theme keyboard activation, panel opening,
  reply-marker toggle, calendar slot reply and scripted voice demo passed. DOM
  widths at 1440 and 390 showed no document overflow. Browser viewport reset.
- Site dependency audit: 0 findings. Extension npm ci reported 73 existing findings
  (4 moderate, 69 high); no dependency upgrade was attempted in this domain slice.
- No model/prompt/migration/auth contract changes and no live Google test.

## External work still pending

- AWS threadly CLI session expired. Asked user for permission to run aws login under
  signing-in-to-aws skill; no answer yet. No EC2 state/IP was verified this turn.
- GoDaddy delegate access to threadly.au verified. DNS still parked; no changes made.
  Need verify current Elastic IP through AWS before setting A @ and A api; www
  already points to threadly.au. Preserve unrelated NS/TXT/mail records.
- User explicitly deferred mailbox creation to their friend. Stop email setup.
- Operator, support contact and final privacy/terms details still missing. Source
  has __LAUNCH_ markers; this mode deliberately cannot deploy them publicly.
- Google project production/verification and new-account sign-in not inspected.
  Current documented setup is Testing with gmail.readonly (restricted scope).
  Do not claim unrestricted public signup from successful hosting alone.
- No DNS, server environment, timers, OAuth config or deployed release changed.

## Resume

Continue with user's supplied design, never restore the rejected landing page.
AWS login confirmation is pending; once authorized run aws login --profile threadly,
verify identity and EC2/SSM state, obtain current Elastic IP and inspect allowlisted
nonsecret runtime config. Prepare exact GoDaddy changes and follow browser tool
confirmation rules for public exposure. API-only public HTTPS can be deployed
independently using existing --public-https while website policy details are pending.
Publish reviewable source changes before deploying a pinned commit. Final public
verification must cover external HTTPS, bundle download, and real new-user OAuth.


## Public deployment continuation

User explicitly requested domain deployment and removal of per-user terminal/tunnel
requirements. AWS login completed under existing threadly profile, account
710507379899. Instance running/SSM Online; verified Elastic IP 15.135.18.234,
allocation eipalloc-0f1a348cb7d0ea855. Security group already allows public TCP 443.
The support inbox is delegated to the user's friend. Website placeholder pages
were replaced with factual early-access data/service notices and an honest pending
support contact. This permits hosting for existing invited testers; it is not a
claim of completed Google verification or unrestricted signup.
