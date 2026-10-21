from pathlib import Path

import streamlit as st

from rlm_lab.execution.events import ExperimentTrace, TraceEvent
from rlm_lab.execution.runners import LabRunner, RunOptions
from rlm_lab.execution.store import RunStore
from rlm_lab.modeling.config import ModelConfig


st.set_page_config(page_title="RLM Lab", layout="wide")


def render_event_timeline(events: list[TraceEvent]) -> None:
    if not events:
        st.info("No events recorded for this run.")
        return

    table = [
        {
            "time": event.timestamp.strftime("%H:%M:%S"),
            "kind": event.kind,
            "runner": event.runner or "",
            "step": event.step or "",
            "details": event.summary,
        }
        for event in events
    ]
    st.dataframe(table, use_container_width=True, hide_index=True)


def render_runner_result(label: str, result) -> None:
    st.subheader(label.capitalize())
    columns = st.columns(4)
    columns[0].metric("Success", "Yes" if result.success else "No")
    columns[1].metric("Latency (ms)", f"{result.metrics.duration_ms:.1f}")
    columns[2].metric("LM Calls", str(result.metrics.call_count))
    columns[3].metric("Total Tokens", str(result.metrics.total_tokens))

    if result.error:
        st.error(result.error)
    else:
        st.write(result.answer or "")

    if result.retrieved_chunks:
        with st.expander(f"{label.capitalize()} retrieved chunks", expanded=False):
            for chunk in result.retrieved_chunks:
                st.markdown(f"**{chunk.path}** `score={chunk.score:.3f}` `chunk={chunk.chunk_id}`")
                st.code(chunk.preview)

    if result.lm_calls:
        with st.expander(f"{label.capitalize()} LM calls", expanded=False):
            for index, call in enumerate(result.lm_calls, start=1):
                st.markdown(
                    f"**Call {index}** `latency={call.latency_ms:.1f}ms` "
                    f"`tokens={call.total_tokens}` `model={call.model}`"
                )
                if call.prompt:
                    st.code(call.prompt)
                elif call.messages:
                    st.json(call.messages)
                st.json(
                    {
                        "outputs": call.outputs,
                        "usage": call.usage,
                    }
                )

    if result.trajectory:
        with st.expander(f"{label.capitalize()} trajectory", expanded=False):
            st.json(result.trajectory)


def render_trace(trace: ExperimentTrace) -> None:
    st.markdown(f"### Run `{trace.run_id}`")
    top = st.columns(4)
    top[0].metric("Mode", trace.runner_mode.upper())
    top[1].metric("Model", trace.model)
    top[2].metric("Prompt Length", str(len(trace.prompt)))
    top[3].metric("Runs Saved", str(len(trace.results)))

    st.text_area("Prompt", trace.prompt, height=120)

    if trace.initial_retrieval:
        with st.expander("Initial retrieval", expanded=False):
            st.json(trace.initial_retrieval.model_dump(mode="json"))

    result_columns = st.columns(max(1, len(trace.results)))
    for column, (name, result) in zip(result_columns, trace.results.items(), strict=False):
        with column:
            render_runner_result(name, result)

    st.markdown("### Event timeline")
    render_event_timeline(trace.events)


def main() -> None:
    st.title("DSPy.RLM Exploratory Lab")
    st.caption("Run recursive and baseline paths side by side, inspect traces, and compare token usage.")

    model_config = ModelConfig.from_env()
    store = RunStore(Path("runs"))
    runner = LabRunner(run_store=store, model_config=model_config)

    with st.sidebar:
        st.header("Configuration")
        st.code(model_config.dspy_model)
        if model_config.missing_env:
            st.warning("Missing model environment: " + ", ".join(model_config.missing_env))

        corpus_dir = st.text_input("Corpus directory", value="")
        retrieval_top_k = st.slider("Retrieval top-k", min_value=1, max_value=10, value=4)
        max_iterations = st.slider("RLM max iterations", min_value=1, max_value=10, value=6)
        max_llm_calls = st.slider("RLM max LLM calls", min_value=1, max_value=12, value=6)
        runner_mode = st.selectbox("Runner", options=["both", "rlm", "baseline"], index=0)

    live_container = st.empty()
    latest_trace_key = "latest_trace"

    with st.form("run-form"):
        prompt = st.text_area(
            "Prompt",
            value="Compare how the recursive runner behaves versus the direct baseline on this task.",
            height=180,
        )
        submitted = st.form_submit_button("Run experiment", type="primary")

    if submitted:
        live_events: list[TraceEvent] = []

        def event_callback(event: TraceEvent) -> None:
            live_events.append(event)
            last_rows = [
                {
                    "time": item.timestamp.strftime("%H:%M:%S"),
                    "kind": item.kind,
                    "runner": item.runner or "",
                    "step": item.step or "",
                    "details": item.summary,
                }
                for item in live_events[-20:]
            ]
            with live_container.container():
                st.markdown("### Live timeline")
                st.dataframe(last_rows, use_container_width=True, hide_index=True)

        options = RunOptions(
            prompt=prompt,
            runner_mode=runner_mode,
            corpus_dir=Path(corpus_dir).expanduser() if corpus_dir.strip() else None,
            retrieval_top_k=retrieval_top_k,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
        )

        try:
            trace = runner.run(options, event_callback=event_callback)
            st.session_state[latest_trace_key] = trace.model_dump(mode="json")
        except Exception as exc:
            st.error(str(exc))

    if latest_trace_key in st.session_state:
        render_trace(ExperimentTrace.model_validate(st.session_state[latest_trace_key]))

    st.markdown("## Saved runs")
    available = store.list_runs(limit=50)
    if not available:
        st.info("No saved runs yet.")
        return

    selected_run = st.selectbox("Replay a saved run", options=[summary.run_id for summary in available])
    selected_trace = store.load_trace(selected_run)
    render_trace(selected_trace)


if __name__ == "__main__":
    main()
