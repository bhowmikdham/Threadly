# Deploy runbook — AWS EC2

For the current **Bedrock staging deployment**, use the
[CloudShell launcher and runbook](ec2/README.md). It creates a separate stack with
SSM administration and no public SSH. The instructions below describe the legacy
Mac/Ollama deployment and must not be combined with the new launcher.

Target: t3.small or larger, Elastic IP attached, Ubuntu LTS.
Reminder: inference NEVER runs on this box (docs/decisions/001).

## One-time setup

1. Security group: inbound 443 (world) + 22 (your IPs). Nothing else.
2. Billing alarm at $20 (CloudWatch -> Billing -> alarm on EstimatedCharges).
3. Install docker + compose plugin; add user to the docker group.
4. Install tailscale, `tailscale up`, confirm the Mac's Ollama answers:
   `curl http://<mac-tailscale-ip>:11434/api/tags`
5. `git clone https://github.com/bhowmikdham/Threadly.git && cd Threadly`
6. `cp .env.example .env` and fill in real values (POSTGRES_PASSWORD, SECRET_KEY,
   FERNET_KEY, Google OAuth creds, OPENROUTER_API_KEY, ELEVENLABS_API_KEY, DOMAIN).
7. Point the domain's DNS A record at the Elastic IP.
8. `make up` — caddy provisions TLS automatically on first request.

## Redeploy

```bash
git pull && make up        # rebuilds changed images; volumes survive
```

## Data

Named volumes: pgdata (postgres), chromadata (chroma), caddy_data (certs).
`docker compose down` keeps them; only `down -v` destroys them — don't.
Backup: `docker compose exec postgres pg_dump -U threadly threadly > backup.sql`

## ML artifacts

`ml/` is mounted read-only into the api container at `/ml`. Weights are NOT in
git — scp them into `ml/classifier/` / `ml/adapter/` on the server (see ml/README.md).
