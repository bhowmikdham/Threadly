# models/ — AI team drop zone

The AI team ships **files, not services**. Backend code loads whatever is in
this directory; everything here is optional and the stack runs without it.

Expected layout (mounted read-only into `inference` and `orchestrator`):

```
models/
├── bert/            # HF-format sequence-classification dir (config.json,
│                    # model.safetensors, tokenizer files, labels in config)
├── adapter/         # QLoRA adapter — applied on the Ollama side (Mac), not here
└── prompts.yaml     # prompt templates; overrides orchestrator defaults
```

- `bert/` is loaded by the **inference** service for `/v1/classify` when
  `transformers` is installed (see `services/inference/requirements-bert.txt`).
  Absent → keyword-rule fallback.
- `prompts.yaml` is loaded by the **orchestrator**. Slot names are the contract:
  see `contracts/CONTRACTS.md` § prompts. Absent → built-in defaults.
- The LoRA `adapter/` never runs on AWS (rule R8: inference stays off the box);
  it lives here only for versioning alongside the artifacts it belongs to.
