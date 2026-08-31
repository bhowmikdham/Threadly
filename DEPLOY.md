# Threadly — Deploy

One script provisions AWS; one script updates a running box. No console
click-ops. Topology per the architecture doc: a single EC2 instance runs the
compose stack, inference lives on the Mac over Tailscale, TLS by Caddy.

## Day 0 — provision everything

On your laptop (needs aws CLI v2 with credentials, an existing EC2 key pair):

```bash
KEY_NAME=my-keypair DOMAIN=api.threadly.example ./deploy/provision.sh
```

That single command, idempotently (safe to re-run):
- creates the security group — inbound **443 + 22 only**
- launches a **t3.small** (Ubuntu 24.04, 30GB gp3), tagged `threadly`
- allocates + associates an **Elastic IP**
- injects `deploy/bootstrap.sh` as user-data, which on first boot installs
  docker, clones the repo, **generates a production `.env`** (random JWT
  secret, internal token, Fernet key, postgres password — dev mode OFF),
  builds and starts the stack, and installs the nightly-backup + weekly
  image-prune cron jobs.

Fail-fast guard: every service refuses to boot in production with missing or
placeholder secrets, so a botched `.env` is a loud crash at start, not a
quietly forgeable JWT.

Watch first boot: `ssh ubuntu@<ip> 'sudo tail -f /var/log/cloud-init-output.log'`

### The four things a script can't do for you

1. **DNS**: point your A record at the printed Elastic IP (Caddy then
   self-provisions the TLS cert on 443 — port 80 stays closed).
2. **Third-party keys**: edit `/opt/threadly/.env` — `GOOGLE_CLIENT_ID/SECRET/
   REDIRECT_URI`, `ELEVENLABS_API_KEY`, `OPENROUTER_API_KEY`,
   `THREADLY_OLLAMA_BASE_URL` — then `sudo docker compose up -d`.
3. **The Mac**: `brew install ollama tailscale`, join the same tailnet,
   `OLLAMA_HOST=0.0.0.0 ollama serve`, pull `qwen3.5:4b`, `qwen3.5:2b`,
   `nomic-embed-text`. QLoRA adapter: `ollama create threadly-4b` from a
   Modelfile with `ADAPTER`, then set `THREADLY_OLLAMA_MODEL=threadly-4b`.
   The EC2 box joins the tailnet too (`tailscale up`); verify with
   `curl http://<mac-ip>:11434/api/tags`.
4. **Billing alarm** ($20) in CloudWatch — billing metrics live in us-east-1.

## Day N — ship an update

```bash
./deploy/update.sh ubuntu@<elastic-ip>
```

Pulls, rebuilds, **applies pending schema migrations** (`scripts/migrate.sh`,
tracked in the `schema_migrations` table — new migration files go in
`infra/postgres/init/` with a `01-`, `02-`… prefix), restarts, and health-checks.

## Operate

- **Logs**: `docker compose logs -f --tail=100` — rotated (10MB × 3 per
  service); one chat turn is greppable across services by its `X-Request-ID`.
- **Backups**: nightly cron via `scripts/backup.sh` (7 kept, `/opt/threadly/backups`)
  — copy off-box periodically. Chroma is derived data; pgdata is what matters.
  Restore: `gunzip -c backup.sql.gz | docker compose exec -T postgres psql -U threadly threadly`
- **Memory**: services carry `mem_limit`s sized for the t3.small (2GB). If the
  OOM killer visits, upgrade to t3.medium before tuning anything else.
- **AI team drop-off**: `scp` files into `/opt/threadly/models/`, then
  `sudo docker compose restart orchestrator inference` (first BERT landing:
  rebuild inference with `--build-arg WITH_BERT=1`).
- **Mac down?** Nothing to do — the 2s health check fails and traffic falls
  back to OpenRouter (PII-masked) automatically; inference `/healthz` shows it.

## Honest caveats

- `deploy/provision.sh` is syntax-checked and reviewed but was written without
  a live AWS account to run against — expect the first run to need small
  fixes (IAM permissions, region quirks). It is idempotent, so iterate freely.
- A private repo needs a read credential on the box: use a tokenized
  `REPO_URL` or install a deploy key before re-running bootstrap.
- The chroma image is deliberately unpinned until the first prod pull; pin the
  resolved version in `docker-compose.yml` right after deploy.
