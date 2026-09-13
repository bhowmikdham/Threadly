# 001 — Inference never runs on AWS

Date: 2026-08-20 · Status: accepted

## Context

The AWS free tier excludes GPU instances, and the student credit balance would be
exhausted within days of continuous inference (risk register R8). The team owns a
Mac M4 16GB that can hold qwen3.5:4b (+LoRA adapter) and qwen3.5:2b resident
in memory without swapping.

## Decision

The EC2 box orchestrates; it never infers. Primary inference is Ollama on the
Mac, reached over a private Tailscale network (:11434). Fallback is OpenRouter
(stock qwen3.5-9b) when the Mac is unreachable, for long-thread summaries that
exceed local context, and as the ablation Tier-1 baseline.

## Consequences

- Module 8 (model client) owns a fallback chain with a 2s health probe.
- Every payload leaving the box for OpenRouter/ElevenLabs is PII-masked first (module 9).
- The Mac being down degrades quality/cost, not availability.
- Deploy runbook includes Tailscale setup on both ends.
