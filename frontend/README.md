# frontend/ — Chrome extension (Plasmo)

This is the extension's home in the monorepo. The current implementation
(login + persistent sign-in) lives on the `frontend` branch, which predates
this structure and has the files at the repo root — it moves in here as part
of merging that branch (see the "relocate extension into frontend/" issue for
the exact `git mv` steps).

Owner: frontend team. Build/run instructions live in the extension's own
README once it lands. CI: `.github/workflows/frontend-ci.yml` (path-filtered,
runs pnpm build against this folder).
