import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))

import replay_histories as rh  # noqa: E402  (eval/replay_histories.py)
from formcast.schema import flatten_schema  # noqa: E402
from formcast.template.model import Field, FormInfo, Template, linked_cells  # noqa: E402
from formcast.template.ops import OpContext, apply  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}}}}
    },
}
CTX = OpContext(schema_paths=flatten_schema(SCHEMA), page_sizes=[(612.0, 792.0)])
# two 9pt widget boxes in the lower half of two 18pt rows; the table's computed cells are whole rows
SEGMENT = {"page": 0, "rect": [72, 300, 300, 360], "row_top": 320, "row_height": 18, "max_rows": 2,
           "columns": [{"key": "name", "x": [72, 300]}]}  # fmt: skip


def _seed():
    fields = [
        Field(id=f"w0_{r}", type="text", page=0, rect=(72, 329 + 18 * r, 300, 338 + 18 * r), origin="widget")
        for r in range(2)
    ]
    form = FormInfo(fingerprint="x", page_count=1, page_sizes=[(612, 792)], kinds=["fillable"])
    return Template(template_id="t", schema=SCHEMA, form=form, fields=fields)


def _old_run():
    """What an agent did under the old ops: a coarse table, then removed the widget fields it covers."""
    t, _ = apply(_seed(), {"op": "add_table", "source_path": "items", "segments": [SEGMENT]}, author="human")
    for fid in ("w0_0", "w0_1"):
        t, _ = apply(t, {"op": "remove_field", "id": fid}, author="human")
    return t.model_copy(update={"history": [h.model_copy(update={"author": "agent"}) for h in t.history]})


def test_replay_refuses_the_removals_and_linking_keeps_each_widget_box():
    guarded, refused = rh.replay_guarded(_seed(), _old_run(), CTX)
    assert refused == {"remove_field": 2}
    assert {f.id for f in guarded.fields} == {"w0_0", "w0_1", "f1"}
    linked, n = rh.link_kept_fields(guarded)
    assert n == 2 and linked_cells(linked) == {"w0_0": ("f1", 0, 0, "name"), "w0_1": ("f1", 0, 1, "name")}
