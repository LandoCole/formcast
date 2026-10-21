from formcast.pagemap.model import Candidate, FormMap, PageMap
from formcast.schema import flatten_schema
from formcast.template.coverage import Coverage, coverage
from formcast.template.model import Field, FormInfo, Source, Template
from formcast.template.ops import OpContext, apply

SIZES = [(612.0, 792.0)]


def _form_map(candidates=()):
    page = PageMap(
        index=0, width=612, height=792, rotation=0, kind="digital", text_source="pdf",
        words=[], lines=[], rules=[], widgets=[], candidates=list(candidates),
    )
    return FormMap(pages=[page], fingerprint="fp")


def _template(schema, fields=()):
    return Template(
        template_id="t",
        schema=schema,
        form=FormInfo(fingerprint="fp", page_count=1, page_sizes=SIZES, kinds=["digital"]),
        fields=list(fields),
    )


def _cand(cid, rect):
    return Candidate(id=cid, kind="text", rect=rect, conf=0.9, detector="test")


def test_unmapped_path_empties_after_set_source():
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    paths = flatten_schema(schema)
    t = _template(schema, [Field(id="f1", type="text", page=0, rect=(100, 100, 200, 120))])
    cov = coverage(t, _form_map(), paths)
    assert isinstance(cov, Coverage)
    assert cov.unmapped_paths == ["name"]
    assert cov.unmapped_fields == ["f1"]
    t, _ = apply(t, {"op": "set_source", "id": "f1", "source": {"paths": ["name"]}}, author="agent",
                 context=OpContext(schema_paths=paths, page_sizes=SIZES))
    cov = coverage(t, _form_map(), paths)
    assert cov.unmapped_paths == [] and cov.unmapped_fields == []


def test_unmapped_paths_cover_leaves_and_table_arrays_not_containers():
    schema = {
        "type": "object",
        "properties": {
            "address": {"type": "object", "properties": {"city": {"type": "string"}}},
            "contact": {"type": "array", "items": {"enum": ["email", "phone"]}},
            "meds": {
                "type": "array",
                "items": {"type": "object", "properties": {"drug": {"type": "string"}}},
            },
        },
    }
    paths = flatten_schema(schema)
    t = _template(schema)
    assert coverage(t, _form_map(), paths).unmapped_paths == ["address.city", "contact", "meds", "meds[].drug"]

    table = Field(
        id="f1", type="table", source=Source(paths=["meds"]),
        segments=[{"page": 0, "rect": (72, 300, 540, 500), "row_top": 300, "row_height": 20,
                   "max_rows": 10, "columns": [{"key": "drug", "x": (72, 540)}]}],
    )
    t = _template(schema, [table])
    assert coverage(t, _form_map(), paths).unmapped_paths == ["address.city", "contact"]


def test_unused_candidates_drop_once_a_field_covers_them():
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    fm = _form_map([_cand("c1", (100, 100, 200, 120)), _cand("c2", (300, 300, 400, 320))])
    t = _template(schema)
    assert coverage(t, fm, flatten_schema(schema)).unused_candidates == ["c1", "c2"]
    t = _template(schema, [Field(id="f1", type="text", page=0, rect=(102, 100, 200, 121))])
    assert coverage(t, fm, flatten_schema(schema)).unused_candidates == ["c2"]


def test_table_cells_cover_candidates():
    schema = {"type": "object", "properties": {}}
    fm = _form_map([_cand("cell", (300, 320, 540, 340)), _cand("row", (72, 340, 540, 360))])
    table = Field(
        id="f1", type="table", source=None,
        segments=[{"page": 0, "rect": (72, 300, 540, 500), "row_top": 320, "row_height": 20,
                   "max_rows": 9, "columns": [{"key": "a", "x": (72, 300)}, {"key": "b", "x": (300, 540)}]}],
    )
    assert coverage(_template(schema, [table]), fm, []).unused_candidates == []


def test_low_confidence():
    schema = {"type": "object", "properties": {}}
    fields = [
        Field(id="a", type="text", page=0, rect=(0, 0, 10, 10), confidence=0.2),
        Field(id="b", type="text", page=0, rect=(0, 20, 10, 30), confidence=0.5),
        Field(id="c", type="text", page=0, rect=(0, 40, 10, 50)),
    ]
    assert coverage(_template(schema, fields), _form_map(), []).low_confidence == ["a"]
