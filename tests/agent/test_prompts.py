import json
import re
from pathlib import Path

from formcast.agent.prompts import SYSTEM_PROMPT, first_user_message
from formcast.agent.report import step_report
from formcast.agent.session import StepRecord
from formcast.llm import Usage
from formcast.pagemap.model import Candidate, FormMap, PageMap, TextLine, Word
from formcast.sandbox import ExecResult
from formcast.schema import flatten_schema
from formcast.template.coverage import Coverage
from formcast.template.model import Field, FormInfo, Source, Template
from formcast.template.op_models import OP_NAMES

REPO = Path(__file__).resolve().parents[2]
GT_DIR = REPO / "forms" / "gt"
ALLOWLIST = {"Name", "Date", "Signature", "Address", "Phone", "City", "State", "Zip", "Email"}

SCHEMA = {
    "type": "object",
    "properties": {
        "person": {
            "type": "object",
            "properties": {
                "full_name": {"type": "string", "description": "Legal name, first and last"},
                "status": {"type": "string", "enum": ["single", "married"]},
            },
        },
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"label": {"type": "string"}, "qty": {"type": "integer"}},
            },
        },
    },
}


def _corpus_labels() -> set[str]:
    labels: set[str] = set()
    for path in sorted(GT_DIR.glob("*.json")) if GT_DIR.is_dir() else []:
        data = json.loads(path.read_text())
        for f in data.get("fields", []):
            label = " ".join(str(f.get("label") or "").split())
            if len(label) > 4 and label not in ALLOWLIST:
                labels.add(label)
    return labels


def test_system_prompt_contains_no_corpus_labels():
    prompt = " ".join(SYSTEM_PROMPT.split())
    leaked = sorted(label for label in _corpus_labels() if label in prompt)
    assert leaked == []


def test_system_prompt_names_the_tools_api_and_rules():
    for needle in ("run_python", "finish", "print(api())", "transforms()", "tpl", "schema", "unplaceable"):
        assert needle in SYSTEM_PROMPT, needle
    for op in OP_NAMES:
        assert re.search(rf"\b{op}\b", SYSTEM_PROMPT), op
    for method in (
        "candidates",
        "find_text",
        "right_of",
        "below",
        "near",
        "region",
        "sublabels_under",
        "label_for",
    ):
        assert f"fm.{method}" in SYSTEM_PROMPT, method
    lowered = SYSTEM_PROMPT.lower()
    for idea in ("points", "top-left", "step report", "sub-label", "table", "checkbox", "never map"):
        assert idea in lowered, idea


def test_system_prompt_stays_lean():
    assert len(SYSTEM_PROMPT) < 12000


def test_first_user_message_renders_schema_and_summary():
    paths = flatten_schema(SCHEMA)
    msg = first_user_message("FORM MAP: 1 page(s) ...", paths, None, None)
    assert "FORM MAP: 1 page(s) ..." in msg
    assert "person.full_name" in msg and "Legal name, first and last" in msg
    assert "single | married" in msg
    assert "items[].qty" in msg and "integer" in msg
    assert "in table items" in msg
    assert "region" not in msg.lower()


def test_first_user_message_with_instruction_and_region():
    paths = flatten_schema(SCHEMA)
    msg = first_user_message("MAP", paths, "Widen the box.", (1, (10.0, 20.25, 30.0, 40.0)))
    assert "Widen the box." in msg
    assert "page 1" in msg and "[10, 20.2, 30, 40]" in msg


# --- step report ---------------------------------------------------------------


def _form_map() -> FormMap:
    line = TextLine(id=0, text="Phone #", rect=(72, 88, 110, 100), word_ids=[0], font_size=12)
    page = PageMap(
        index=0, width=612, height=792, rotation=0, kind="digital", text_source="pdf",
        words=[Word(text="Phone #", rect=(72, 88, 110, 100), line_id=0, source="pdf")],
        lines=[line], rules=[], widgets=[],
        candidates=[Candidate(id="c0_4", kind="text", rect=(120, 86, 300, 100), conf=0.9,
                              detector="fake", label_line_id=0, label_relation="left")],
    )  # fmt: skip
    return FormMap(pages=[page])


def _template() -> Template:
    return Template(
        template_id="t",
        schema=SCHEMA,
        form=FormInfo(fingerprint="", page_count=1, page_sizes=[(612, 792)], kinds=["digital"]),
        fields=[
            Field(
                id="f1", type="text", page=0, rect=(1, 1, 50, 20), source=Source(paths=["person.full_name"])
            ),
            Field(id="f2", type="text", page=0, rect=(1, 30, 50, 50)),
        ],
    )


def test_step_report_lists_everything():
    record = StepRecord(
        n=3,
        code="...",
        exec=ExecResult(stdout="hello there\n", truncated=True, error="Traceback...\nNameError: x"),
        applied=[{"op": "add_field", "id": "f2", "type": "text"}],
        rejected=[{"op": {"op": "move", "id": "zz", "dx": 1}, "reason": "unknown field id 'zz'"}],
        warnings=["field 'f2' overlaps field 'f1'"],
        usage=Usage(),
    )
    cov = Coverage(
        unmapped_paths=["person.status", "items"],
        unused_candidates=["c0_4"],
        unmapped_fields=["f2"],
        mappable_count=5,
    )
    report = step_report(record, cov, template=_template(), form_map=_form_map())
    assert "hello there" in report and "truncated" in report
    assert "NameError: x" in report
    assert "f2 add_field" in report
    assert "unknown field id 'zz'" in report and "move" in report
    assert "overlaps field 'f1'" in report
    assert "fields: 2" in report and "1 with a source" in report
    assert "schema paths: 3 of 5 mapped" in report
    assert "person.status" in report and "items" in report
    assert 'c0_4 "Phone #"' in report


def test_step_report_caps_long_lists():
    cov = Coverage(unmapped_paths=[f"p{i}" for i in range(60)], unused_candidates=[])
    record = StepRecord(n=1, code="", exec=ExecResult(stdout="", truncated=False), applied=[], rejected=[],
                        warnings=[], usage=Usage())  # fmt: skip
    report = step_report(record, cov, template=_template(), form_map=_form_map())
    assert "p39" in report and "p40" not in report
    assert "20 more" in report


def test_step_report_counts_come_from_coverage():
    from formcast.template.coverage import coverage

    paths = flatten_schema(SCHEMA)
    cov = coverage(_template(), _form_map(), paths)
    record = StepRecord(n=1, code="", exec=ExecResult(stdout="", truncated=False), applied=[], rejected=[],
                        warnings=[], usage=Usage())  # fmt: skip
    report = step_report(record, cov, template=_template(), form_map=_form_map())
    # mappable: full_name, status, items (table), items[].label, items[].qty; full_name is mapped
    assert cov.mappable_count == 5
    assert "schema paths: 1 of 5 mapped" in report


def test_system_prompt_explains_summary_notation():
    assert "fm.sublabels_under(" in SYSTEM_PROMPT and "fm.right_of(" in SYSTEM_PROMPT
    assert "+rule_above" in SYSTEM_PROMPT and "+rule`" in SYSTEM_PROMPT
    assert "only in the form summary" in SYSTEM_PROMPT


def test_prompt_endings_match_the_summarizer():
    from formcast.pagemap.summarize import PROMPT_END

    for ch in ":#?":
        assert PROMPT_END.search(f"Label{ch}")
        assert f'"{ch}"' in SYSTEM_PROMPT


def test_region_with_no_overlapping_fields_says_none():
    paths = flatten_schema(SCHEMA)
    msg = first_user_message("MAP", paths, "Fix it.", (0, (400.0, 400.0, 500.0, 500.0)), template=_template())
    assert "TEMPLATE FIELDS IN THE REGION: none." in msg


def test_step_report_states_the_budget_left_when_given():
    record = StepRecord(n=7, code="", exec=ExecResult(stdout="", truncated=False), applied=[], rejected=[],
                        warnings=[], usage=Usage())  # fmt: skip
    cov = Coverage(mappable_count=1)
    plain = step_report(record, cov, template=_template(), form_map=_form_map())
    assert "budget" not in plain
    report = step_report(record, cov, template=_template(), form_map=_form_map(), budget=(7, 40))
    assert "budget: step 7 of 40, 33 left" in report
