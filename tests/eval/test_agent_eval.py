import json
import sys
import zlib
from pathlib import Path

import numpy as np
import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))

import agent_eval  # noqa: E402  (eval/agent_eval.py)
import baseline_detection  # noqa: E402
import score  # noqa: E402
from formcast.llm import ImagePart, LLMResponse, Message, TextPart, ToolCall, Usage  # noqa: E402
from formcast.pagemap.ocr import render  # noqa: E402
from formcast.template.model import Field, FormInfo, Source, Template  # noqa: E402

A = (10.0, 10.0, 110.0, 30.0)
B = (10.0, 50.0, 110.0, 70.0)


# ---------- .env loading and secret scrubbing ----------


def test_load_eval_env_loads_only_the_requested_eval_prefixes(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "ANTHROPIC_EVAL_API_KEY='sk-ant-secret'\n"
        "export ANTHROPIC_EVAL_MODEL=claude-x\n"
        "OPENAI_EVAL_API_KEY=sk-openai\n"
        "OTHER_API_KEY=nope\n"
        "ANTHROPIC_API_KEY=not-an-eval-key\n"
    )
    for k in ("ANTHROPIC_EVAL_API_KEY", "ANTHROPIC_EVAL_MODEL", "OPENAI_EVAL_API_KEY", "OTHER_API_KEY",
              "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    loaded = agent_eval.load_eval_env(["ANTHROPIC_EVAL"], env)
    assert sorted(loaded) == ["ANTHROPIC_EVAL_API_KEY", "ANTHROPIC_EVAL_MODEL"]
    import os

    assert os.environ["ANTHROPIC_EVAL_API_KEY"] == "sk-ant-secret"
    assert os.environ["ANTHROPIC_EVAL_MODEL"] == "claude-x"
    assert "OPENAI_EVAL_API_KEY" not in os.environ and "OTHER_API_KEY" not in os.environ
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_load_eval_env_refuses_a_prefix_that_is_not_an_eval_config(tmp_path):
    with pytest.raises(ValueError, match="_EVAL"):
        agent_eval.load_eval_env(["OPENAI"], tmp_path / ".env")


def test_redactor_scrubs_secrets_from_nested_values():
    r = agent_eval.Redactor(["sk-secret-123456", ""])
    out = r.scrub({"a": ["error: bad key sk-secret-123456!"], "b": {"c": "fine"}, "n": 3})
    assert out == {"a": ["error: bad key ***!"], "b": {"c": "fine"}, "n": 3}


# ---------- fake-scan input ----------


def test_fake_scan_pdf_is_image_only_and_matches_the_baseline_fake_scan(fillable_form_pdf):
    data = agent_eval.fake_scan_pdf("ff", fillable_form_pdf)
    doc = pymupdf.open(stream=data, filetype="pdf")
    src = pymupdf.open(fillable_form_pdf)
    assert doc.page_count == src.page_count
    page = doc[0]
    assert page.rect == src[0].rect
    assert page.get_text().strip() == ""
    assert not list(page.widgets() or [])
    assert len(page.get_images()) == 1

    src_page = src[0]
    for w in list(src_page.widgets()):
        src_page.delete_widget(w)
    expected = baseline_detection.fake_scan(render(src_page, agent_eval.DPI), seed=zlib.crc32(b"ff:0"))
    got = render(page, agent_eval.DPI)
    assert got.size == expected.size
    diff = np.abs(np.asarray(got.convert("L"), dtype=int) - np.asarray(expected.convert("L"), dtype=int))
    assert diff.mean() < 1.0


# ---------- scoring helpers ----------


def _template(fields):
    return Template(
        template_id="t",
        schema={},
        form=FormInfo(fingerprint="x", page_count=2, page_sizes=[(612, 792)] * 2, kinds=["digital"] * 2),
        fields=fields,
    )


def test_detection_scores_count_only_pages_that_have_ground_truth():
    gt = [score.GTField(0, A, "text"), score.GTField(0, B, "choice")]
    t = _template([
        Field(id="f1", type="text", page=0, rect=A),
        Field(id="f2", type="text", page=1, rect=A),  # unscored page: ignored
        Field(id="f3", type="text", page=0, rect=(300, 300, 400, 320)),  # false positive
    ])  # fmt: skip
    out = agent_eval.detection_scores(gt, t)
    s = out["iou0.3"]
    assert (s["gt"], s["pred"], s["matched"]) == (2, 2, 1)
    assert s["recall"] == pytest.approx(0.5) and s["precision"] == pytest.approx(0.5)
    assert s["missed_by_type"]["choice"] == 1
    assert set(out) == {"iou0.3", "iou0.5"}


def test_baseline_detector_is_ffdnet_for_digital_and_ffdetr_for_scans():
    assert agent_eval.baseline_detector("fillable", "as_published") == "FFDNet-L"
    assert agent_eval.baseline_detector("flat", "as_published") == "FFDNet-L"
    assert agent_eval.baseline_detector("flat", "fake_scan") == "FFDetr"
    assert agent_eval.baseline_detector("scanned", "as_published") == "FFDetr"


def _brow(form, page, variant, det, gt, pred, m3, m5, t3=None, t5=None):
    return {
        "form": form, "page": page, "variant": variant, "detector": det, "gt": gt, "pred": pred,
        "matched@0.3": m3, "matched@0.5": m5, "type_ok@0.3": m3 if t3 is None else t3,
        "type_ok@0.5": m5 if t5 is None else t5,
    }  # fmt: skip


def test_baseline_scores_sum_the_pages_of_one_form_variant_and_detector():
    rows = [
        _brow("x", 0, "as_published", "FFDNet-L", 10, 8, 6, 5, t3=5),
        _brow("x", 1, "as_published", "FFDNet-L", 10, 12, 10, 9),
        _brow("x", 0, "as_published", "FFDetr", 10, 1, 1, 1),
        _brow("x", 0, "fake_scan", "FFDNet-L", 10, 1, 1, 1),
        _brow("y", 0, "as_published", "FFDNet-L", 10, 1, 1, 1),
        {"form": "x", "page": 2, "variant": "as_published", "detector": "FFDNet-L", "gt": None, "pred": 4},
    ]
    out = agent_eval.baseline_scores(rows, "x", "as_published", "FFDNet-L")
    assert out["detector"] == "FFDNet-L"
    s3 = out["iou0.3"]
    assert (s3["gt"], s3["pred"], s3["matched"], s3["type_ok"]) == (20, 20, 16, 15)
    assert s3["recall"] == pytest.approx(0.8) and s3["precision"] == pytest.approx(0.8)
    assert s3["type_acc"] == pytest.approx(15 / 16)
    assert out["iou0.5"]["matched"] == 14
    assert agent_eval.baseline_scores(rows, "z", "as_published", "FFDNet-L") is None


def _row(variant, gt, pred, matched, n_path, path_ok, tin, tout, base_matched=None):
    det = {"gt": gt, "pred": pred, "matched": matched, "type_ok": matched}
    base = None if base_matched is None else {
        "detector": "FFDNet-L",
        **{k: {"gt": gt, "pred": gt, "matched": base_matched, "type_ok": base_matched} for k in ("iou0.3", "iou0.5")},
    }  # fmt: skip
    return {
        "variant": variant,
        "detection": {"iou0.3": dict(det), "iou0.5": dict(det)},
        "baseline": base,
        "mapping": {"n_path": n_path, "n_transform": 0, "matched": n_path, "path_ok": path_ok,
                    "transform_ok": 0, "joint_ok": path_ok, "exact_ok": path_ok},
        "steps": 3, "input_tokens": tin, "output_tokens": tout, "wall_s": 10.0, "finished": True,
    }  # fmt: skip


def test_totals_are_micro_averaged_per_variant_and_overall():
    rows = [
        _row("as_published", 10, 10, 9, 4, 4, 100, 10, base_matched=8),
        _row("as_published", 30, 20, 15, 6, 3, 200, 20, base_matched=27),
        _row("fake_scan", 10, 10, 5, 0, 0, 50, 5),
    ]
    out = agent_eval.totals(rows)
    ap = out["as_published"]
    assert ap["forms"] == 2
    assert ap["detection"]["iou0.3"]["recall"] == pytest.approx(24 / 40)
    assert ap["detection"]["iou0.3"]["precision"] == pytest.approx(24 / 30)
    assert ap["baseline"]["iou0.3"]["recall"] == pytest.approx(35 / 40)
    assert ap["mapping"]["path_acc"] == pytest.approx(7 / 10)
    assert ap["mapping"]["joint_acc"] == pytest.approx(7 / 10)
    assert (ap["input_tokens"], ap["output_tokens"]) == (300, 30)
    assert out["all"]["forms"] == 3 and out["all"]["input_tokens"] == 350
    assert out["fake_scan"]["baseline"] is None  # no baseline rows to compare


def test_totals_skip_rows_that_failed_before_scoring():
    rows = [_row("as_published", 10, 10, 9, 4, 4, 100, 10), {"variant": "as_published", "error": "boom",
                                                              "detection": None, "mapping": None}]
    out = agent_eval.totals(rows)
    assert out["as_published"]["forms"] == 1 and out["as_published"]["failed"] == 1


# ---------- recording provider ----------


class _FakeProvider:
    def __init__(self, replies):
        self.replies = list(replies)
        self.closed = False

    def complete(self, messages, tools):
        return self.replies.pop(0)

    def close(self):
        self.closed = True


def _reply(code):
    call = ToolCall(id="1", name="run_python", arguments={"code": code}, raw_arguments=json.dumps({"code": code}))
    return LLMResponse(text="thinking", tool_calls=[call], usage=Usage(100, 10), raw={})


def test_recording_provider_keeps_each_reply_and_the_report_it_got_back():
    inner = _FakeProvider([_reply("print(1)"), _reply("print(2)")])
    rec = agent_eval.RecordingProvider(inner)
    sys_msg, first = Message(role="system", content="S"), Message(role="user", content="FIRST")
    tools = [object()]  # session calls carry tools; a call with none is a page-pass call
    r1 = rec.complete([sys_msg, first], tools)
    rec.complete([sys_msg, first, r1.to_message(), Message(role="tool", content="STEP 1 REPORT", tool_call_id="1")], tools)
    rec.close()
    assert inner.closed
    assert rec.first_user_message == "FIRST"
    assert [c["tool_calls"][0]["arguments"]["code"] for c in rec.calls] == ["print(1)", "print(2)"]
    assert rec.calls[0]["feedback"] == [] and rec.calls[1]["feedback"] == ["STEP 1 REPORT"]
    assert rec.calls[0]["usage"] == {"input_tokens": 100, "output_tokens": 10}
    assert rec.reports() == ["STEP 1 REPORT", None]


# ---------- CLI helpers and the report ----------

MANIFEST = [
    {"id": "a", "split": "dev", "kind": "flat"},
    {"id": "b", "split": "dev", "kind": "scanned"},
    {"id": "h", "split": "heldout", "kind": "fillable"},
]


def test_select_forms_keeps_the_split_and_refuses_forms_of_another_split():
    assert [f["id"] for f in agent_eval.select_forms(MANIFEST, "dev", None)] == ["a", "b"]
    assert [f["id"] for f in agent_eval.select_forms(MANIFEST, "dev", "b, a")] == ["b", "a"]
    with pytest.raises(ValueError, match="h"):
        agent_eval.select_forms(MANIFEST, "dev", "a,h")


def test_variants_skip_fake_scans_of_scanned_forms():
    flat, scanned = MANIFEST[0], MANIFEST[1]
    assert agent_eval.variants_for(flat, "both") == ["as_published", "fake_scan"]
    assert agent_eval.variants_for(scanned, "both") == ["as_published"]
    assert agent_eval.variants_for(scanned, "fake_scan") == []


def test_parse_args_accepts_repeated_and_comma_separated_model_envs():
    args = agent_eval.parse_args(
        ["--split", "dev", "--model-env", "ANTHROPIC_EVAL,OPENAI_EVAL", "--model-env", "X_EVAL", "--tag", "r1"]
    )
    assert args.model_env == ["ANTHROPIC_EVAL", "OPENAI_EVAL", "X_EVAL"]
    assert (args.jobs, args.variant, args.tag) == (3, "as_published", "r1")


def test_run_name_appends_a_filename_safe_tag():
    assert agent_eval._run_name("2026-10-21", None) == "2026-10-21"
    assert agent_eval._run_name("2026-10-21", "round 1/b") == "2026-10-21_round-1-b"


def test_markdown_lists_each_row_and_the_totals():
    rows = [
        {**_row("as_published", 10, 10, 9, 4, 4, 100, 10, base_matched=8), "form": "x", "kind": "flat",
         "stop_reason": "finish", "error": None, "unplaceable": 1},
        {"form": "y", "kind": "flat", "variant": "as_published", "detection": None, "mapping": None,
         "error": "harness: Boom: x"},
    ]  # fmt: skip
    for r in rows[:1]:
        for k in ("iou0.3", "iou0.5"):
            r["detection"][k].update(recall=0.9, precision=0.9, type_acc=1.0)
            r["baseline"][k].update(recall=0.8, precision=0.8, type_acc=1.0)
        r["mapping"].update(path_acc=1.0, transform_acc=0.0, joint_acc=1.0, exact_path_set_acc=1.0)
    meta = {"model": "m", "model_env": "X_EVAL", "split": "dev", "run": "2026-10-21_t", "api_format": "responses",
            "tag": "t", "commit": "abc", "budget": {}, "baseline_file": "b.json"}
    md = agent_eval.render_markdown(meta, rows, agent_eval.totals(rows))
    assert "| x | flat | as_published | 10 | 10 | 0.90 (0.90) |" in md
    assert "harness: Boom: x" in md
    assert "| as_published | 1 (1) |" in md
    assert "false positives" in md


def test_totals_precision_counts_matched_boxes_not_matched_gt():
    # a multi-line field matched 5 GT line boxes but is one predicted box
    row = _row("as_published", 10, 6, 9, 4, 4, 100, 10)
    for k in ("iou0.3", "iou0.5"):
        row["detection"][k]["pred_matched"] = 5
    out = agent_eval.totals([row])["as_published"]["detection"]["iou0.3"]
    assert out["recall"] == pytest.approx(9 / 10)
    assert out["precision"] == pytest.approx(5 / 6, abs=1e-4)


def test_form_maps_given_to_the_agent_do_not_carry_the_file_name():
    from formcast.pagemap.model import FormMap
    from formcast.pagemap.summarize import summarize

    fm = FormMap(pages=[], fingerprint="x", source_name="some_known_form_fake_scan.pdf")
    anon = agent_eval.anonymized(fm)
    assert anon.source_name == agent_eval.ANON_SOURCE_NAME and anon.fingerprint == "x"
    assert "some_known_form" not in summarize(anon)


def test_totals_leave_out_sessions_that_ended_in_an_error_and_list_them():
    bad = {**_row("as_published", 10, 10, 9, 4, 4, 100, 10), "form": "cut", "error": "usage limit"}
    good = {**_row("as_published", 10, 10, 9, 4, 4, 100, 10), "form": "ok"}
    out = agent_eval.totals([good, bad])
    assert out["all"]["forms"] == 1 and out["all"]["failed"] == 1
    assert out["all"]["detection"]["iou0.3"]["gt"] == 10  # the cut-off session's template is not counted
    assert out["all"]["errored"] == ["cut/as_published"]
    assert out["all"]["input_tokens"] == 200  # spend is still counted


def test_parse_args_modes_that_need_no_model():
    seed = agent_eval.parse_args(["--split", "dev", "--seed-only"])
    assert seed.seed_only and seed.model_env == []
    rescore = agent_eval.parse_args(["--rescore", "eval/results/x.json"])
    assert rescore.rescore == "eval/results/x.json" and rescore.split is None
    for bad in (["--split", "dev"], ["--model-env", "A_EVAL"], ["--rescore", "x", "--seed-only"]):
        with pytest.raises(SystemExit):
            agent_eval.parse_args(bad)


def test_rescore_with_missing_templates_says_to_extract_the_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_eval, "ROOT", tmp_path)
    monkeypatch.setattr(agent_eval, "TEMPLATES_ARCHIVE", tmp_path / "eval/results/templates.tar.gz")
    results = tmp_path / "eval/results/agent_x_dev_run.json"
    results.parent.mkdir(parents=True)
    agent_eval.TEMPLATES_ARCHIVE.write_bytes(b"")
    row = {"form": "f", "variant": "as_published", "template_path": "eval/results/templates/run/x/f_as_published.json"}
    results.write_text(json.dumps({"meta": {}, "rows": [row]}))
    before = results.read_text()
    args = agent_eval.parse_args(["--rescore", str(results)])
    with pytest.raises(SystemExit) as exc:
        agent_eval.rescore_main(args)
    msg = str(exc.value)
    assert "1 of 1 saved template" in msg and "f_as_published.json" in msg
    assert "tar -xzf eval/results/templates.tar.gz" in msg
    assert results.read_text() == before  # nothing rewritten


# ---------- fill correctness ----------


def _fill(units, correct, rows_dropped_ok=True):
    part = {"units": units, "correct": correct}
    return {"units": units, "correct": correct, "fill_acc": correct / units, "text": dict(part),
            "choice": {"units": 0, "correct": 0}, "cell": {"units": 0, "correct": 0},
            "rows_dropped_ok": rows_dropped_ok, "fill_errors": []}  # fmt: skip


def test_totals_include_fill_acc():
    rows = [
        {**_row("as_published", 10, 10, 9, 4, 4, 100, 10), "fill": _fill(10, 5)},
        {**_row("as_published", 10, 10, 9, 4, 4, 100, 10), "fill": _fill(30, 27, rows_dropped_ok=False)},
        {**_row("fake_scan", 10, 10, 9, 4, 4, 100, 10), "fill": _fill(40, 0, rows_dropped_ok=None)},
        _row("fake_scan", 10, 10, 9, 4, 4, 100, 10),  # scored before fill_acc existed: left out of the fill figure
    ]
    out = agent_eval.totals(rows)
    ap = out["as_published"]["fill"]
    assert (ap["forms"], ap["units"], ap["correct"]) == (2, 40, 32)
    assert ap["fill_acc"] == pytest.approx(32 / 40)
    assert ap["text"] == {"units": 40, "correct": 32, "acc": pytest.approx(0.8)}
    assert ap["rows_dropped"] == {"checked": 2, "ok": 1}
    assert ap["static_choice"] == 0
    rows[0]["fill"]["static_choice"] = 2
    assert agent_eval.totals(rows)["as_published"]["fill"]["static_choice"] == 2
    assert out["all"]["fill"]["fill_acc"] == pytest.approx(32 / 80)
    assert out["fake_scan"]["fill"]["forms"] == 1
    assert agent_eval.totals([_row("as_published", 10, 10, 9, 4, 4, 100, 10)])["all"]["fill"] is None


def test_markdown_has_fill_column():
    row = {**_row("as_published", 10, 10, 9, 4, 4, 100, 10), "form": "x", "kind": "flat", "stop_reason": "finish",
           "error": None, "unplaceable": 0, "fill": _fill(8, 6)}  # fmt: skip
    old = {**row, "form": "z"}
    del old["fill"]
    for r in (row, old):
        for k in ("iou0.3", "iou0.5"):
            r["detection"][k] = {**r["detection"][k], "recall": 0.9, "precision": 0.9, "type_acc": 1.0}
        r["mapping"] = {**r["mapping"], "path_acc": 1.0, "transform_acc": 0.0, "joint_acc": 0.5,
                        "exact_path_set_acc": 1.0}  # fmt: skip
    meta = {"model": "m", "model_env": "X_EVAL", "split": "dev", "run": "r", "api_format": "responses", "tag": None,
            "commit": "abc", "budget": {}, "baseline_file": "b.json"}  # fmt: skip
    md = agent_eval.render_markdown(meta, [row, old], agent_eval.totals([row, old]))
    headers = [line for line in md.splitlines() if line.startswith("| form |") or line.startswith("| variant |")]
    assert len(headers) == 2 and all("| joint | fill |" in h for h in headers)
    assert "| 0.50 | 0.75 |" in md  # x: joint, then fill
    assert "| 0.50 | – |" in md  # z has no fill figure
    assert "fill_acc" in md or "fill:" in md  # the notes explain the column
    assert "static_choice" in md


def test_score_row_adds_fill_scored_on_the_variant_pdf(tmp_path, monkeypatch, flat_form_pdf):
    forms, gt_dir = tmp_path / "forms", tmp_path / "gt"
    (forms / "dev").mkdir(parents=True)
    (forms / "schemas").mkdir()
    gt_dir.mkdir()
    (forms / "dev" / "ff.pdf").write_bytes(flat_form_pdf.read_bytes())
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    (forms / "schemas" / "ff.json").write_text(json.dumps(schema))
    name_box = [120.0, 86.0, 300.0, 100.0]
    (gt_dir / "ff.json").write_text(json.dumps(
        {"form": "ff", "fields": [{"page": 0, "rect": name_box, "type": "text", "path": "name"}]}))  # fmt: skip
    monkeypatch.setattr(agent_eval, "FORMS", forms)
    monkeypatch.setattr(score, "GT_DIR", gt_dir)
    form = {"id": "ff", "split": "dev", "kind": "flat"}
    t = Template(
        template_id="t", schema=schema,
        form=FormInfo(fingerprint="x", page_count=1, page_sizes=[(612, 792)], kinds=["digital"]),
        fields=[Field(id="n", type="text", page=0, rect=tuple(name_box), source=Source(paths=["name"]))],
    )  # fmt: skip
    for variant in ("as_published", "fake_scan"):
        row = {"form": "ff", "variant": variant}
        agent_eval.score_row(row, t, form)
        assert row["fill"]["units"] == 2 and row["fill"]["fill_acc"] == 1.0, variant  # K = 2 records
        assert row["mapping"]["path_acc"] == 1.0


def test_run_one_keeps_the_step_log_when_scoring_fails(tmp_path, monkeypatch):
    import types

    import formcast
    from formcast.pagemap.model import FormMap

    forms = tmp_path / "forms"
    (forms / "schemas").mkdir(parents=True)
    (forms / "schemas" / "ff.json").write_text(json.dumps({"type": "object", "properties": {}}))
    fm_path = tmp_path / "ff.formmap.json"
    fm_path.write_text(FormMap(pages=[], fingerprint="x", source_name="form.pdf").model_dump_json())
    t = Template(template_id="t", schema={"type": "object", "properties": {}},
                 form=FormInfo(fingerprint="x", page_count=1, page_sizes=[(612, 792)], kinds=["digital"]))  # fmt: skip
    result = types.SimpleNamespace(template=t, steps=[], usage=types.SimpleNamespace(input_tokens=5, output_tokens=1),
                                   stop_reason="finish", finished=True, error=None, unplaceable=[], summary="done")  # fmt: skip
    monkeypatch.setattr(agent_eval, "ROOT", tmp_path)
    monkeypatch.setattr(agent_eval, "FORMS", forms)
    monkeypatch.setattr(agent_eval.LLMConfig, "from_env", classmethod(lambda cls, p: types.SimpleNamespace(model="m")))
    monkeypatch.setattr(agent_eval, "make_provider", lambda cfg: _FakeProvider([]))
    monkeypatch.setattr(formcast, "build_template", lambda *a, **k: result, raising=False)

    def boom(*a, **k):
        raise RuntimeError("scorer broke")

    monkeypatch.setattr(agent_eval, "score_row", boom)
    job = {
        "form": {"id": "ff", "split": "dev", "kind": "flat"}, "variant": "as_published", "model_env": "X_EVAL",
        "prepared": {"pdf_path": str(tmp_path / "ff.pdf"), "formmap_path": str(fm_path), "formmap_s": 0.1,
                     "page_kinds": [], "text_sources": [], "candidates": 0},
        "run_dir": str(tmp_path / "logs"), "templates_dir": str(tmp_path / "templates"),
        "overlays_dir": str(tmp_path / "overlays"),
    }  # fmt: skip
    row = agent_eval.run_one(job)
    assert "scorer broke" in row["error"]
    log = tmp_path / "logs" / "x_eval" / "ff_as_published.json"
    assert log.is_file() and json.loads(log.read_text())["summary"] == "done"  # the session's log survived
    assert row["log_path"] and row["template_path"]


# ---------- pipelines, reachable score, spend guard ----------


def test_the_env_fallback_needs_a_checkout_three_levels_below_the_root(tmp_path, monkeypatch):
    assert agent_eval.env_fallback(Path("/main/.claude/worktrees/w")) == Path("/main/.env")
    assert agent_eval.env_fallback(Path("/repo")) is None and agent_eval.env_fallback(Path("/a/b")) is None
    monkeypatch.setattr(agent_eval, "ENV_PATH", tmp_path / ".env")  # missing
    monkeypatch.setattr(agent_eval, "ENV_FALLBACK", None)
    assert agent_eval.load_eval_env(["ZZ_EVAL"]) == []


def test_load_eval_env_falls_back_to_the_main_checkouts_env(tmp_path, monkeypatch):
    fallback = tmp_path / "main" / ".env"
    fallback.parent.mkdir()
    fallback.write_text("ZZ_EVAL_API_KEY=sk-from-main\n")
    monkeypatch.delenv("ZZ_EVAL_API_KEY", raising=False)
    monkeypatch.setattr(agent_eval, "ENV_PATH", tmp_path / "worktree" / ".env")  # missing
    monkeypatch.setattr(agent_eval, "ENV_FALLBACK", fallback)
    assert agent_eval.load_eval_env(["ZZ_EVAL"]) == ["ZZ_EVAL_API_KEY"]
    monkeypatch.delenv("ZZ_EVAL_API_KEY")
    own = tmp_path / "worktree" / ".env"
    own.parent.mkdir()
    own.write_text("ZZ_EVAL_API_KEY=sk-own\n")  # the repo's own .env wins when it exists
    agent_eval.load_eval_env(["ZZ_EVAL"])
    import os

    assert os.environ["ZZ_EVAL_API_KEY"] == "sk-own"
    monkeypatch.delenv("ZZ_EVAL_API_KEY")
    assert agent_eval.load_eval_env(["ZZ_EVAL"], tmp_path / "nowhere" / ".env") == []  # an explicit path has no fallback


def test_env_fallback_is_the_main_checkout_above_the_worktree():
    assert agent_eval.ENV_FALLBACK == agent_eval.ROOT.parents[2] / ".env"


def test_pipeline_flags():
    assert agent_eval.pipeline_flags("agent") == {"vision": False}
    assert agent_eval.pipeline_flags("pages") == {"vision": True, "agent": False}
    assert agent_eval.pipeline_flags("pages+agent") == {"vision": True}
    assert set(agent_eval.PIPELINES) == {"agent", "pages", "pages+agent"}


def test_parse_args_pipeline_and_max_usd():
    base = ["--split", "dev", "--model-env", "A_EVAL"]
    default = agent_eval.parse_args(base)
    assert (default.pipeline, default.max_usd) == ("agent", None)
    args = agent_eval.parse_args([*base, "--pipeline", "pages+agent", "--max-usd", "1.5"])
    assert (args.pipeline, args.max_usd) == ("pages+agent", 1.5)
    for bad in (["--pipeline", "vision"], ["--max-usd", "0"], ["--max-usd", "lots"]):
        with pytest.raises(SystemExit):
            agent_eval.parse_args([*base, *bad])


def test_run_cost_haiku_and_unknown_model():
    haiku = {"model": "claude-haiku-5-5", "input_tokens": 2_000_000, "output_tokens": 1_000_000}
    assert agent_eval.run_cost([haiku]) == pytest.approx(0.2 + 0.5)
    assert agent_eval.run_cost([haiku, haiku]) == pytest.approx(1.4)
    assert agent_eval.run_cost([]) == 0.0
    assert agent_eval.run_cost([haiku, {"form": "no-session", "error": "prepare: boom"}]) == pytest.approx(0.7)
    assert agent_eval.run_cost([haiku, {"model": "other-model", "input_tokens": 1, "output_tokens": 1}]) is None


def test_reachable_scores_count_only_gt_a_seed_box_overlaps():
    gt = [score.GTField(0, A, "text", path="p.a", transforms=["upper"]), score.GTField(0, B, "text", path="p.b"),
          score.GTField(0, FAR_BOX, "text", path="p.far")]  # fmt: skip
    seed = _template([Field(id="s1", type="text", page=0, rect=A), Field(id="s2", type="text", page=0, rect=B)])
    done = _template([Field(id="s1", type="text", page=0, rect=A, source=Source(paths=["p.a"])),
                      Field(id="s2", type="text", page=0, rect=B, source=Source(paths=["p.x"])),
                      Field(id="new", type="text", page=0, rect=FAR_BOX, source=Source(paths=["p.far"]))])  # fmt: skip
    out = agent_eval.reachable_scores(gt, done, seed)
    # p.a: path right, transform missing; p.b: wrong path; p.far: right but no seed box there
    assert out == {"n": 2, "path_ok": 1, "joint_ok": 0, "path_acc": 0.5, "joint_acc": 0.0}


FAR_BOX = (300.0, 300.0, 400.0, 320.0)


def test_score_row_adds_mapping_reachable_only_with_a_seed(tmp_path, monkeypatch, flat_form_pdf):
    forms, gt_dir = tmp_path / "forms", tmp_path / "gt"
    (forms / "dev").mkdir(parents=True)
    (forms / "schemas").mkdir()
    gt_dir.mkdir()
    (forms / "dev" / "ff.pdf").write_bytes(flat_form_pdf.read_bytes())
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    (forms / "schemas" / "ff.json").write_text(json.dumps(schema))
    name_box = [120.0, 86.0, 300.0, 100.0]
    (gt_dir / "ff.json").write_text(json.dumps(
        {"form": "ff", "fields": [{"page": 0, "rect": name_box, "type": "text", "path": "name"}]}))  # fmt: skip
    monkeypatch.setattr(agent_eval, "FORMS", forms)
    monkeypatch.setattr(score, "GT_DIR", gt_dir)
    form = {"id": "ff", "split": "dev", "kind": "flat"}
    box = tuple(name_box)
    t = _template([Field(id="n", type="text", page=0, rect=box, source=Source(paths=["name"]))]).model_copy(
        update={"schema_": schema})
    seed = _template([Field(id="n", type="text", page=0, rect=box)])
    bare = {"form": "ff", "variant": "as_published"}
    agent_eval.score_row(bare, t, form)
    assert "mapping_reachable" not in bare
    row = {"form": "ff", "variant": "as_published"}
    agent_eval.score_row(row, t, form, seed=seed)
    assert row["mapping_reachable"] == {"n": 1, "path_ok": 1, "joint_ok": 1, "path_acc": 1.0, "joint_acc": 1.0}


def test_totals_micro_average_mapping_reachable():
    def reach(n, path_ok, joint_ok):
        return {"n": n, "path_ok": path_ok, "joint_ok": joint_ok, "path_acc": 0.0, "joint_acc": 0.0}

    rows = [
        {**_row("as_published", 10, 10, 9, 4, 4, 100, 10), "mapping_reachable": reach(4, 4, 3)},
        {**_row("as_published", 10, 10, 9, 6, 3, 100, 10), "mapping_reachable": reach(6, 3, 3)},
        {**_row("fake_scan", 10, 10, 9, 4, 4, 100, 10), "mapping_reachable": reach(10, 5, 0)},
        _row("fake_scan", 10, 10, 9, 4, 4, 100, 10),  # an old row without the figure is left out
        {**_row("fake_scan", 10, 10, 9, 4, 4, 100, 10), "mapping_reachable": reach(99, 99, 99), "error": "cut"},
    ]
    out = agent_eval.totals(rows)
    ap = out["as_published"]["mapping_reachable"]
    assert ap == {"n": 10, "path_ok": 7, "joint_ok": 6, "path_acc": 0.7, "joint_acc": 0.6}
    assert out["fake_scan"]["mapping_reachable"]["n"] == 10  # the errored row's figure is not counted
    assert out["all"]["mapping_reachable"]["path_acc"] == pytest.approx(12 / 20)
    assert agent_eval.totals([_row("as_published", 10, 10, 9, 4, 4, 100, 10)])["all"]["mapping_reachable"] is None


def test_totals_sum_page_pass_tokens_and_page_errors():
    rows = [{**_row("as_published", 10, 10, 9, 4, 4, 100, 10), "page_pass_input_tokens": 60,
             "page_pass_output_tokens": 6, "page_errors": 1},
            _row("as_published", 10, 10, 9, 4, 4, 100, 10)]  # fmt: skip
    out = agent_eval.totals(rows)["all"]
    assert (out["page_pass_input_tokens"], out["page_pass_output_tokens"], out["page_errors"]) == (60, 6, 1)


def test_markdown_title_names_the_pipeline_and_totals_show_reachable():
    row = {**_row("as_published", 10, 10, 9, 4, 4, 100, 10), "form": "x", "kind": "flat", "stop_reason": "finish",
           "error": None, "unplaceable": 0, "page_pass_input_tokens": 60, "page_pass_output_tokens": 6,
           "page_errors": 1, "mapping_reachable": {"n": 4, "path_ok": 3, "joint_ok": 2, "path_acc": 0.75,
                                                    "joint_acc": 0.5}}  # fmt: skip
    for k in ("iou0.3", "iou0.5"):
        row["detection"][k] = {**row["detection"][k], "recall": 0.9, "precision": 0.9, "type_acc": 1.0}
    row["mapping"] = {**row["mapping"], "path_acc": 1.0, "transform_acc": 0.0, "joint_acc": 0.5,
                      "exact_path_set_acc": 1.0}  # fmt: skip
    meta = {"model": "m", "model_env": "X_EVAL", "split": "dev", "run": "r_pages", "api_format": "responses",
            "tag": None, "commit": "abc", "budget": {}, "baseline_file": "b.json", "pipeline": "pages",
            "cost_usd": 0.1234}  # fmt: skip
    md = agent_eval.render_markdown(meta, [row], agent_eval.totals([row]))
    assert md.splitlines()[0].endswith("r_pages, pipeline pages")
    assert "| 0.75 (0.50) |" in md and "mapping_reachable" in md
    assert "Page pass: 60 in / 6 out tokens" in md and "1 page(s)" in md
    assert "$0.12" in md
    old = agent_eval.render_markdown({k: v for k, v in meta.items() if k != "pipeline"}, [row], agent_eval.totals([row]))
    assert old.splitlines()[0].endswith("pipeline agent")


def test_recording_provider_text_for_image_messages_and_page_calls():
    class Inner:
        def __init__(self):
            self.seen = []
            self.replies = [_reply("page-call"), _reply("print(1)"), _reply("print(2)")]

        def complete(self, messages, tools, **kw):
            self.seen.append((tools, kw))
            return self.replies.pop(0)

        def close(self):
            pass

    inner = Inner()
    rec = agent_eval.RecordingProvider(inner)
    page_msg = Message(role="user", content=[TextPart("PAGE 0"), ImagePart(b"x" * 10, "image/png")])
    first = Message(role="user", content=[TextPart("FIRST"), ImagePart(b"y" * 7, "image/png")])
    sys_msg = Message(role="system", content="S")
    rec.complete([sys_msg, page_msg], [], max_output_tokens=32000, timeout_s=300)  # page pass: no tools
    r1 = rec.complete([sys_msg, first], [object()])
    follow_up = Message(role="user", content=[TextPart("see page"), ImagePart(b"z" * 3, "image/png")])
    rec.complete([sys_msg, first, r1.to_message(), Message(role="tool", content="REPORT", tool_call_id="1"), follow_up],
                 [object()])  # fmt: skip
    assert inner.seen[0] == ([], {"max_output_tokens": 32000, "timeout_s": 300})  # keyword arguments forwarded
    assert rec.first_user_message == "FIRST\n\n[image: 7 bytes]"  # the first session message, not the page's
    assert [c["text"] for c in rec.page_calls] == ["thinking"] and set(rec.page_calls[0]) == {"text", "usage", "seconds"}
    assert rec.page_calls[0]["usage"] == {"input_tokens": 100, "output_tokens": 10}
    assert len(rec.calls) == 2 and rec.calls[1]["feedback"] == ["REPORT", "see page\n\n[image: 3 bytes]"]
    assert rec.reports() == ["REPORT\n---\nsee page\n\n[image: 3 bytes]", None]
    assert "x" * 10 not in json.dumps(rec.calls + rec.page_calls)


def test_run_jobs_stops_submitting_once_the_cost_passes_max_usd():
    from concurrent.futures import ThreadPoolExecutor

    ran = []

    def fake_run(job):
        ran.append(job["form"]["id"])
        return {"form": job["form"]["id"], "model": "claude-haiku-5-5", "model_env": "A_EVAL", "variant": "v",
                "input_tokens": 10_000_000, "output_tokens": 0}  # $1 each  # fmt: skip

    def job(i, prepared=None):
        return {"form": {"id": f"f{i}", "split": "dev", "kind": "flat"}, "variant": "v", "model_env": "A_EVAL",
                "baseline": None, "prepared": prepared or {}, "pipeline": "pages"}  # fmt: skip

    jobs = [job(0), job(1, {"error": "prepare: boom"}), job(2), job(3), job(4)]
    seen = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        rows = agent_eval.run_jobs(pool, jobs, 1, 1.5, run=fake_run, on_row=seen.append)
    assert ran == ["f0", "f2"]  # $1.00 then $2.00 passes the cap before f3
    by_form = {r["form"]: r for r in rows}
    assert by_form["f1"]["error"] == "prepare: boom"
    assert by_form["f3"]["error"] == by_form["f4"]["error"] == agent_eval.SKIPPED == "skipped: spend limit"
    assert by_form["f3"]["pipeline"] == "pages" and by_form["f3"]["detection"] is None
    assert seen == rows and len(rows) == 5
    # no cap: everything runs; an unpriced model: nothing is skipped either
    ran.clear()
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = agent_eval.run_jobs(pool, [job(i) for i in range(4)], 2, None, run=fake_run)
    assert sorted(ran) == ["f0", "f1", "f2", "f3"] and not any(r.get("error") for r in rows)
    ran.clear()
    unpriced = lambda j: {**fake_run(j), "model": "mystery"}  # noqa: E731
    with ThreadPoolExecutor(max_workers=1) as pool:
        rows = agent_eval.run_jobs(pool, [job(i) for i in range(3)], 1, 0.01, run=unpriced)
    assert len(ran) == 3 and not any(r.get("error") for r in rows)


def test_run_one_passes_the_pipeline_flags_and_logs_the_page_pass_without_images(tmp_path, monkeypatch):
    import types

    import formcast
    from formcast.agent.session import SessionResult
    from formcast.pagemap.model import FormMap
    from formcast.pagepass.run import PageOutcome, PagePassResult

    forms = tmp_path / "forms"
    (forms / "schemas").mkdir(parents=True)
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    (forms / "schemas" / "ff.json").write_text(json.dumps(schema))
    fm_path = tmp_path / "ff.formmap.json"
    fm_path.write_text(FormMap(pages=[], fingerprint="x", source_name="form.pdf").model_dump_json())
    t = Template(template_id="t", schema=schema,
                 form=FormInfo(fingerprint="x", page_count=1, page_sizes=[(612, 792)], kinds=["digital"]))  # fmt: skip
    pages = [PageOutcome(0, "ok", applied=3, rejected=[{"op": "set_source", "reason": "no such field"}],
                         usage=Usage(50, 5), seconds=1.234, text='{"mappings": []}'),
             PageOutcome(1, "unparsed", usage=Usage(40, 4), text="prose"),
             PageOutcome(2, "error", error="timeout")]  # fmt: skip
    result = SessionResult(template=t, finished=True, summary="page pass only", steps=[], usage=Usage(90, 9),
                           stop_reason="finish", page_pass=PagePassResult(t, pages, Usage(90, 9)))  # fmt: skip
    seen = {}

    def fake_build(*a, **k):
        seen["kwargs"] = k
        return result

    def fake_score(row, tpl, form, pdf=None, seed=None):
        seen["seed"] = seed
        return None

    monkeypatch.setattr(agent_eval, "ROOT", tmp_path)
    monkeypatch.setattr(agent_eval, "FORMS", forms)
    monkeypatch.setattr(agent_eval.LLMConfig, "from_env", classmethod(lambda cls, p: types.SimpleNamespace(model="m")))
    monkeypatch.setattr(agent_eval, "make_provider", lambda cfg: _FakeProvider([]))
    monkeypatch.setattr(formcast, "build_template", fake_build, raising=False)
    monkeypatch.setattr(agent_eval, "score_row", fake_score)
    monkeypatch.setattr(formcast, "preview", lambda *a, **k: [])
    monkeypatch.setattr(agent_eval, "open_pdf", lambda p: types.SimpleNamespace(close=lambda: None))
    job = {
        "form": {"id": "ff", "split": "dev", "kind": "flat"}, "variant": "as_published", "model_env": "X_EVAL",
        "pipeline": "pages",
        "prepared": {"pdf_path": str(tmp_path / "ff.pdf"), "formmap_path": str(fm_path), "formmap_s": 0.1,
                     "page_kinds": [], "text_sources": [], "candidates": 0},
        "run_dir": str(tmp_path / "logs"), "templates_dir": str(tmp_path / "templates"),
        "overlays_dir": str(tmp_path / "overlays"),
    }  # fmt: skip
    row = agent_eval.run_one(job)
    assert not row.get("error")
    assert {k: v for k, v in seen["kwargs"].items() if k in ("vision", "agent")} == {"vision": True, "agent": False}
    assert seen["seed"].fields == []  # the seed: new_template on the run's form map
    assert row["pipeline"] == "pages" and row["formmap_path"] == "ff.formmap.json"
    assert (row["page_pass_input_tokens"], row["page_pass_output_tokens"], row["page_errors"]) == (90, 9, 2)
    log = json.loads((tmp_path / "logs" / "x_eval" / "ff_as_published.json").read_text())
    assert log["pipeline"] == "pages" and log["steps"] == []
    assert [p["page"] for p in log["page_pass"]] == [0, 1, 2]
    assert log["page_pass"][0] == {
        "page": 0, "status": "ok", "applied": 3, "rejected": [{"op": "set_source", "reason": "no such field"}],
        "usage": {"input_tokens": 50, "output_tokens": 5}, "seconds": 1.23, "text": '{"mappings": []}', "error": None,
    }  # fmt: skip
    assert log["page_pass"][2]["error"] == "timeout"


def test_seed_template_uses_the_saved_form_map_or_rebuilds_it(tmp_path, monkeypatch):
    import formcast.pagemap.build as build_mod
    from formcast.pagemap.model import FormMap

    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    fm = FormMap(pages=[], fingerprint="saved", source_name="form.pdf")
    (tmp_path / "fm.json").write_text(fm.model_dump_json())
    rebuilt = []

    def fake_build(pdf, **kw):
        rebuilt.append(pdf)
        return FormMap(pages=[], fingerprint="rebuilt", source_name="real_name.pdf")

    monkeypatch.setattr(agent_eval, "ROOT", tmp_path)
    monkeypatch.setattr(build_mod, "build_form_map", fake_build)
    monkeypatch.setattr(agent_eval, "variant_pdf", lambda form, variant: b"pdf")
    form = {"id": "ff", "split": "dev", "kind": "flat"}
    saved = agent_eval._seed_template({"variant": "as_published", "formmap_path": "fm.json"}, form, schema)
    assert saved.form.fingerprint == "saved" and not rebuilt
    old = agent_eval._seed_template({"variant": "fake_scan"}, form, schema)  # a row from before the path was kept
    assert old.form.fingerprint == "rebuilt" and rebuilt == [b"pdf"]
    gone = agent_eval._seed_template({"variant": "as_published", "formmap_path": "missing.json"}, form, schema)
    assert gone.form.fingerprint == "rebuilt"
