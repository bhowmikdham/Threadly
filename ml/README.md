# ml/ — AI team drop zone

The AI team ships **files, not services**. Backend code loads everything in this
directory; nothing here runs on its own. The whole directory is mounted read-only
into the api container at `/ml` (see docker-compose.yml).

## The contract

| Path                          | What                                         | In git? | Loaded by |
|-------------------------------|----------------------------------------------|---------|-----------|
| `classifier/bert.safetensors` | BERT reply-or-not / priority classifier      | NO — weights delivered out-of-band | sync pipeline (classification at ingest) |
| `classifier/labels.json`      | label index -> name mapping for the classifier | yes   | same |
| `adapter/`                    | QLoRA adapter for qwen3.5:4b                 | NO — applied on the Mac via Ollama Modelfile, kept here for versioning reference | Ollama (Mac), not the api |
| `prompts/prompts.yaml`        | versioned prompt templates                   | yes     | orchestrator / model client |

Weights (`*.safetensors`, `*.bin`, `*.gguf`) are gitignored at the repo root.
Deliver them via a GitHub release asset or drive link, then `scp` into place on
the server (see `infra/deploy/README.md`).

## Rules

1. Changes to `labels.json` or `prompts.yaml` go by PR touching ONLY `ml/` —
   backend reviews, because backend code loads these files.
2. Never rename these paths without a matching backend PR: the paths are config
   (`ML_DIR` + conventions in `backend/app/config.py`).
3. Prompt changes bump the `version:` field in `prompts.yaml` so ablation runs
   can pin exact prompt versions.
