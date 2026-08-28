# Threadly — EC2 Deploy Runbook

Target topology from the architecture doc: one EC2 box running the compose
stack, inference on the Mac over Tailscale, TLS by Caddy.

## 1. Provision (once)

- **EC2**: t3.small or larger, Ubuntu 24.04, 30GB gp3.
- **Security group**: inbound 443 and 22 only (80 optional for ACME HTTP-01;
  Caddy prefers TLS-ALPN on 443, so 80 can stay closed).
- **Elastic IP**: allocate + associate; point your DNS A record at it.
- **Billing alarm**: CloudWatch billing alarm at $20.

```bash
# on the box
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu && newgrp docker
git clone https://github.com/bhowmikdham/Threadly.git && cd Threadly
```

## 2. Configure

```bash
cp .env.example .env
```

Production values that must change:

| var | value |
|---|---|
| `THREADLY_DEV_MODE` | `false` |
| `THREADLY_DOMAIN` | your DNS name (Caddy auto-provisions the cert) |
| `THREADLY_JWT_SECRET` / `THREADLY_INTERNAL_TOKEN` | long random strings (`openssl rand -hex 32`) |
| `THREADLY_FERNET_KEY` | `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `POSTGRES_PASSWORD` + same in `THREADLY_DATABASE_URL` | random |
| `GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI` | from the GCP OAuth client (test mode, 100 users) |
| `THREADLY_OLLAMA_BASE_URL` | `http://<mac-tailscale-ip>:11434` |
| `OPENROUTER_API_KEY` | fallback when the Mac is down |
| `ELEVENLABS_API_KEY` | voice |

## 3. Mac inference host

```bash
# on the Mac (M4 16GB)
brew install ollama tailscale
tailscale up                      # same tailnet as the EC2 box
OLLAMA_HOST=0.0.0.0 ollama serve  # listens on :11434
ollama pull qwen3.5:4b && ollama pull qwen3.5:2b && ollama pull nomic-embed-text
# QLoRA adapter (from models/adapter/): create a Modelfile FROM qwen3.5:4b
# with ADAPTER, then `ollama create threadly-4b` and set
# THREADLY_OLLAMA_MODEL=threadly-4b in .env
```

On the EC2 box: `tailscale up`, then verify
`curl http://<mac-ip>:11434/api/tags`. Inference traffic never leaves the
tailnet unmasked; OpenRouter fallback is PII-masked by the inference service.

## 4. Launch + verify

```bash
docker compose up -d --build
docker compose ps                          # everything healthy
curl -s https://$DOMAIN/healthz            # gateway + db
docker compose exec inference python -c "print('ok')"  # container sanity
```

`GET /healthz` on the inference service reports which backends are live
(`ollama: up|down|unconfigured`).

## 5. Operate

- **Logs**: `docker compose logs -f --tail=100` (one chat turn is greppable by
  its `X-Request-ID` across services).
- **Update**: `git pull && docker compose up -d --build` (volumes survive).
- **Backup** (nightly cron):
  `docker compose exec -T postgres pg_dump -U threadly threadly | gzip > backup-$(date +%F).sql.gz`
  — keep the last 7, copy off-box. Chroma is derived data (re-embeddable from
  postgres), so pgdata is the only thing that must survive.
- **AI team drop-off**: `scp` new files into `models/`, then
  `docker compose restart orchestrator inference` (or rebuild inference with
  `--build-arg WITH_BERT=1` the first time BERT lands).
- **Mac down?** Nothing to do: the 2s health check fails and traffic falls
  back to OpenRouter automatically; `/healthz` on inference shows it.
