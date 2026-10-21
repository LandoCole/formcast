# formcast

formcast turns blank PDF forms into field templates. It detects form fields with
`commonforms`, then uses an LLM agent to name, type and structure them.

## Setup

```sh
uv venv -p 3.12 .venv
uv pip install -p .venv/bin/python -e ".[ocr,dev]"
.venv/bin/pytest -q
```

API keys go in `.env` (git-ignored): `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`.

Tests marked `slow` (model downloads) and `llm` (real model calls) are skipped by
default; run them with `-m slow` / `-m llm`.

## Spec

Design spec: [docs/superpowers/specs/2026-10-20-form-projection-design.md](docs/superpowers/specs/2026-10-20-form-projection-design.md)
Plan: [docs/superpowers/plans/2026-10-20-formcast-agent-layer.md](docs/superpowers/plans/2026-10-20-formcast-agent-layer.md)
