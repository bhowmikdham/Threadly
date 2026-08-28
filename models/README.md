# models/ — AI team drop zone

The AI team ships **files, not services**. Backend code loads whatever is in
this directory; everything here is optional and the stack runs without it.

Expected layout (mounted read-only into `inference` and `orchestrator`):

```
models/
├── classifiers/
│   ├── priority/    # HF sequence-classification dirs (config.json,
│   ├── action/      # model.safetensors, tokenizer files); labels come
│   └── category/    # from config.json id2label
├── bert/            # optional: chat INTENT classifier (legacy location)
├── adapter/         # QLoRA adapter — applied on the Ollama side (Mac), not here
└── prompts.yaml     # prompt templates; overrides orchestrator defaults
```

- `classifiers/*` are loaded by the **inference** service and applied to every
  inbound email at sync time (needs `transformers` — rebuild inference with
  `--build-arg WITH_BERT=1`). Absent → keyword-rule fallbacks, so the pipeline
  runs end-to-end either way.
- **Action labels (agreed with backend)**: `approve, review, edit,
  complete_submit, attend, reply, no_action` — one per email, multiclass.
- **Deadline is not a classifier**: the backend extracts deadlines into
  commitments at sync time.
- `prompts.yaml` is loaded by the **orchestrator**. Slot names are the
  contract: see `contracts/CONTRACTS.md` § prompts. Absent → built-in defaults.
- The LoRA `adapter/` never runs on AWS (rule R8: inference stays off the box);
  it lives here only for versioning alongside the artifacts it belongs to.
