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


# --- wider leak check: every text the model can be shown ------------------------------

# Modules whose string literals can reach the model: prompts and messages, step reports,
# refusals, op rejections and warnings, the api() listing and help() of fm methods, the
# summary, coverage and transform docs. Module docstrings are skipped (never shown).
MODEL_FACING = [
    "src/formcast/agent/prompts.py", "src/formcast/agent/session.py", "src/formcast/agent/report.py",
    "src/formcast/pagepass/prompts.py", "src/formcast/data/ai.py",
    "src/formcast/template/ops.py", "src/formcast/template/coverage.py", "src/formcast/sandbox/worker.py",
    "src/formcast/pagemap/query.py", "src/formcast/pagemap/summarize.py", "src/formcast/transforms.py",
]  # fmt: skip
# Schema property and widget names that are plain English words any form uses.
GENERIC_NAMES = {
    "address", "city", "code", "conditions", "date", "description", "details", "from", "height", "kind",
    "name", "number", "per", "position", "reason", "reported", "result", "reviewer", "source", "state",
    "status", "title", "to", "type", "types", "uses", "valid", "where", "year", "yes", "zip",
}  # fmt: skip


def _string_literals(path: Path) -> list[str]:
    import ast

    tree = ast.parse(path.read_text())
    body = tree.body
    module_doc = body[0].value if body and isinstance(body[0], ast.Expr) else None
    return [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n is not module_doc
    ]  # fmt: skip


def _model_facing_text() -> str:
    from formcast.sandbox.worker import api_text

    parts = [SYSTEM_PROMPT, api_text()]
    for rel in MODEL_FACING:
        parts.extend(_string_literals(REPO / rel))
    return " ".join(" ".join(parts).split())


def _schema_property_names() -> set[str]:
    names: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in (node.get("properties") or {}).items():
                names.add(key)
                walk(value)
            for key in ("items", "additionalProperties"):
                walk(node.get(key))
            for key in ("anyOf", "oneOf", "allOf"):
                for sub in node.get(key) or []:
                    walk(sub)
            for sub in (node.get("$defs") or node.get("definitions") or {}).values():
                walk(sub)

    for path in sorted((REPO / "forms" / "schemas").glob("*.json")):
        walk(json.loads(path.read_text()))
    return names


def _widget_names() -> set[str]:
    names: set[str] = set()
    for path in sorted(GT_DIR.glob("*.json")) if GT_DIR.is_dir() else []:
        names |= set(json.loads(path.read_text()).get("widget_paths", {}))
    return names


def _whole_word(word: str, text: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])", text) is not None


def test_no_model_facing_text_contains_corpus_labels():
    text = _model_facing_text()
    assert sorted(label for label in _corpus_labels() if label in text) == []


def test_no_model_facing_text_names_a_corpus_schema_property():
    text = _model_facing_text()
    names = _schema_property_names()
    assert names, "no schemas found"
    leaked = sorted(n for n in names if n.lower() not in GENERIC_NAMES and _whole_word(n, text))
    assert leaked == []


def test_no_model_facing_text_names_a_corpus_widget():
    text = _model_facing_text()
    leaked = sorted(n for n in _widget_names() if n.lower() not in GENERIC_NAMES and _whole_word(n, text))
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
    assert "steps left" not in plain
    report = step_report(record, cov, template=_template(), form_map=_form_map(), budget=(7, 40))
    assert "(step 7 of 40; 33 steps left)" in report


def test_step_report_shortens_long_reasons_after_the_first_few():
    long = "x" * 400
    rejected = [{"op": {"op": "set_label", "id": f"f{i}", "label": "a"}, "reason": f"r{i} " + long} for i in range(5)]
    record = StepRecord(n=1, code="", exec=ExecResult(stdout="", truncated=False), applied=[], rejected=rejected,
                        warnings=[], usage=Usage())  # fmt: skip
    report = step_report(record, Coverage(), template=_template(), form_map=_form_map())
    assert report.count(long) == 3 and "r4 " in report


def test_page_pass_note_is_in_the_after_page_pass_message():
    from formcast.agent.prompts import PAGE_PASS_NOTE

    paths = flatten_schema(SCHEMA)
    msg = first_user_message("MAP", paths, None, None, template=_template(), after_page_pass=True)
    assert PAGE_PASS_NOTE in msg and "STARTING TEMPLATE" not in msg
    assert msg.index(PAGE_PASS_NOTE) < msg.index("CURRENT TEMPLATE: 2 field(s), 1 with a source")
    assert "f1 text page 0 [1, 1, 50, 20] label='' source=person.full_name" in msg
    plain = first_user_message("MAP", paths, None, None, template=_template())
    assert PAGE_PASS_NOTE not in plain and "CURRENT TEMPLATE" in plain


def test_page_pass_note_names_the_structural_work_left_to_the_agent():
    from formcast.agent.prompts import PAGE_PASS_NOTE

    note = " ".join(PAGE_PASS_NOTE.split())
    assert "split that box into one box per label" in note
    assert "add a box for it" in note
    assert "a heading-row box taken as row 1 shifts every record by one row" in note


def test_page_image_parts_put_a_vision_note_then_a_page_line_before_each_image():
    from formcast.agent.prompts import VISION_NOTE, page_image_parts
    from formcast.llm import ImagePart, TextPart

    t = _template()
    t.fields += [Field(id="c1_7", type="checkbox", page=1, rect=(1, 1, 9, 9)),
                 Field(id="c1_8", type="text", page=1, rect=(20, 1, 90, 9))]  # fmt: skip
    assert page_image_parts([(0, b"a"), (1, b"b")], t) == [
        TextPart(VISION_NOTE),
        TextPart("PAGE 0: marked image (boxes as at the start of this session); boxes are tagged with their full ids"),
        ImagePart(b"a"),
        TextPart("PAGE 1: marked image (boxes as at the start of this session); a box marked 7 is c1_7"),
        ImagePart(b"b"),
    ]  # fmt: skip
    assert page_image_parts([], t) == []


def test_vision_note_explains_the_images_and_their_marks():
    from formcast.agent.prompts import VISION_NOTE

    note = " ".join(VISION_NOTE.split()).lower()
    assert "this session also has an image of each page" in note
    assert "replaces what the system prompt says about not seeing the page image" in note
    assert "cannot see" in SYSTEM_PROMPT and "the page image" in SYSTEM_PROMPT  # the statement it overrides
    colours = ("blue a text box", "green a checkbox", "teal a radio option", "purple a signature", "red any remaining")
    assert all(c in note for c in colours)
    assert "mark" in note
