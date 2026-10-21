# RLM Lab

`RLM Lab` is a small exploratory project for running DSPy's experimental `dspy.RLM` side by side with a direct baseline while inspecting recursion steps, retrieval activity, and token usage.

## Features

- `dspy.RLM` runner with explicit iteration and interpreter events
- Baseline `dspy.Predict` runner for side-by-side comparison
- Local file retrieval over `.txt`, `.md`, and `.json` corpora
- Streamlit UI for live inspection and replay of saved traces
- CLI for single prompts, scenario sets, and trace export
- JSON and JSONL run artifacts under `runs/`

## Setup

Create a virtual environment and install the project dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .[dev]
```

Set your model environment:

```bash
export OPENAI_API_KEY=...
export OPENAI_MODEL=gpt-5.4
# Optional:
export OPENAI_BASE_URL=https://api.openai.com/v1
```

## Run The UI

```bash
streamlit run app.py
```

## Run The CLI

Single prompt:

```bash
python -m rlm_lab.run prompt --prompt "Summarize the local corpus" --runner both --corpus-dir tests/fixtures/corpus
```

Scenario set:

```bash
python -m rlm_lab.run scenario --set starter
```

Export saved run summaries:

```bash
python -m rlm_lab.run export --format csv --output runs/summary.csv
```

## Notes

- The recursive path uses DSPy's `RLM`, but this project supplies a local Python interpreter so you can experiment without requiring Deno.
- The interpreter intentionally supports only a constrained set of builtins and helper modules because the executed code is model-generated.
- Token and cost metadata depend on the model provider. OpenAI-compatible endpoints work best for the current instrumentation.
