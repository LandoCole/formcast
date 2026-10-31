"""Export a finished batch run as xlsx, Parquet or CSV, with the field definitions beside the data.

    export(Results.load("out"), [template], "extracted.xlsx", format="xlsx")   # one workbook
    export(Results.load("out"), [template], "extracted", format="csv")         # a folder of files

The export has four parts, the same in every format:

* ``values``: one row per document read, the document columns (``document_id``, ``document_file``, ``document_template``, ``document_match``,
  ``extracted_at``) then one column per schema path with the cleaned value (empty when there is none);
* ``status``: the same rows and columns, each cell holding that field's status in :data:`STATUS_WORDS`' words (``found``, ``doubtful``,
  ``failed_check`` ...), so a value and the doubt about it stay together;
* ``fields``: one row per value column, ``column, path, title, definition, type, checks``, written from the
  template's schema, so a reader of the data (or the fill flow) knows what each column means and what it was
  checked against;
* ``set_aside``: one row per failed document with ``reason`` (``no template fits`` or why it could not be read),
  ``closest_template`` and ``closest_score``. A failed document is never a row of ``values`` or ``status``.

``export(..., names={doc_id: name})`` adds a ``document_name`` column after ``document_id`` to ``values``, ``status`` and
``set_aside``, in every format: the name a person knows each document by (the workbench's uploads are stored by
number, so their ``doc_id`` is ``3.pdf``), or the ``doc_id`` itself for a document the map does not name. A field
whose column would be ``document_name`` is then refused as a document column. :func:`check_columns` makes the same checks
on the templates alone, so a template can be refused before a run rather than at its export.

xlsx is one workbook with a sheet per part; Parquet and CSV are a folder with ``values``, ``status``, ``fields`` and
``set_aside`` files. Column order follows the schema: top-level fields in schema order, and the outermost repeated
group item by item (``owners_1_name``, ``owners_1_percent``, ``owners_2_name`` ...), as ``concrete_paths`` gives
them. A group nested inside another is field by field within its outer item (``concrete_paths`` orders only the
outermost group item by item). With several templates the value columns are the union of their columns, first
template first; a document leaves empty the columns of the other templates, and a column two templates share is
described once, by the first. A path whose column is a document column (``document_file``, ``document_id`` ...), or one column
made by two different paths or types, is refused: the template author renames the path.

Cells hold what was read, not what a spreadsheet would make of it: a text value that starts with ``=`` is stored as
text in xlsx, not as a formula (it is not escaped in CSV, so a program that opens the CSV may treat it as one). A
control character xlsx cannot hold is dropped from that cell. A value that is a list or an object is written as
JSON text. Parquet columns are typed from the values (text, whole numbers, decimals or true/false); a column
whose values mix those is written as text, as is one with a whole number too big for 64 bits (or, beside decimals,
beyond 2**53, which a decimal cannot hold exactly). The Parquet table is built in memory.
"""

from __future__ import annotations

import csv
import importlib
import json
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any, Literal

from formcast.errors import FormcastError
from formcast.extract.batch import Results, column_name
from formcast.extract.model import _DOC_COLUMNS, NAME_COLUMN, ExtractionTemplate, _expand, _max_items, concrete_paths
from formcast.schema import ARRAY_ITEM, SchemaPath, _enum_values, _normalize, flatten_schema, format_path, parse_path

ExportFormat = Literal["xlsx", "parquet", "csv"]

#: What the ``status`` part calls each status.
STATUS_WORDS = {
    "found": "found",
    "blank": "blank",
    "not_located": "not_located",
    "missing": "missing",  # a run made before blank and not_located were told apart
    "unsure": "doubtful",
    "failed_check": "fails_a_check",
    "absent": "not_applicable",
    "not_on_form": "not_applicable",
}

#: The columns of the ``fields`` part and of the ``set_aside`` part.
FIELD_COLUMNS = ("column", "path", "title", "definition", "type", "checks")
SET_ASIDE_COLUMNS = ("document_id", "document_file", "reason", "closest_template", "closest_score")

_FORMATS = ("xlsx", "parquet", "csv")
_PARTS = ("values", "status", "fields", "set_aside")
_INT64 = 2**63
_EXACT_FLOAT = 2**53


# ------------------------------------------------------------------ the extras


def _need(module: str, label: str, extra: str) -> Any:
    """The module ``module``, or a ``FormcastError`` naming the extra that provides it."""
    try:
        return importlib.import_module(module)
    except ImportError:
        raise FormcastError(f"{label} export needs the {extra} extra: pip install 'formcast[{extra}]'") from None


# --------------------------------------------------------------- field definitions


def _leaf(sp: SchemaPath) -> bool:
    return not (sp.type == "object" or (sp.type == "array" and sp.item_type == "object"))


def _facets(node: dict) -> list[str]:
    """The pattern, format, range and length limits of a schema node, as text."""
    out: list[str] = []
    if isinstance(node.get("pattern"), str):
        out.append(f"pattern {node['pattern']}")
    if isinstance(node.get("format"), str):
        out.append(f"format: {node['format']}")
    for key, label in (
        ("minimum", "minimum"), ("exclusiveMinimum", "exclusive minimum"),
        ("maximum", "maximum"), ("exclusiveMaximum", "exclusive maximum"),
        ("multipleOf", "multiple of"), ("minLength", "min length"), ("maxLength", "max length"),
        ("minItems", "min items"), ("maxItems", "max items"),
    ):  # fmt: skip
        value = node.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(f"{label}: {value}")
    return out


def _node(schema: dict, segments: Sequence[str]) -> dict:
    """The (normalized) schema node at ``segments``."""
    node, _ = _normalize(schema, schema, ())
    for seg in segments:
        if seg is ARRAY_ITEM:
            node, _ = _normalize(node.get("items"), schema, ())
        else:
            node, _ = _normalize((node.get("properties") or {}).get(seg), schema, ())
    return node


def _required(schema: dict, segments: list[str]) -> str | None:
    """``"required"`` when every document must have the path, ``"required when ..."`` when only a document that has
    its enclosing group must, else ``None``."""
    required_here = False  # is the segment just walked required in its parent?
    optional: str | None = None  # the deepest enclosing group a document may leave out
    for i, seg in enumerate(segments):
        if seg is ARRAY_ITEM:  # an item of a list may be missing (a list's minItems is not tracked)
            required_here = False
            optional = f"required in each item of {format_path(segments[:i])}"
        else:
            names = _node(schema, segments[:i]).get("required")
            required_here = isinstance(names, list) and seg in names
            if not required_here and i < len(segments) - 1:
                optional = f"required when {format_path(segments[: i + 1])} is given"
    if not required_here:
        return None
    return optional or "required"


def _checks(schema: dict, sp: SchemaPath) -> str:
    """What a value of ``sp`` is checked against, as ``required; pattern ...; enum: a, b; ...`` (empty if nothing)."""
    segments = parse_path(sp.path)
    node = _node(schema, segments)
    parts: list[str] = []
    required = _required(schema, segments)
    if required:
        parts.append(required)
    facets = _facets(node)
    values = _enum_values(node)
    if sp.type == "array":  # a list of plain values: the list's limits, then its items'
        item = _node(schema, [*segments, ARRAY_ITEM])
        facets, values = facets + _facets(item), _enum_values(item)
    parts.extend(f for f in facets if f.startswith("pattern"))
    if values is not None:
        parts.append("enum: " + ", ".join(values))
    parts.extend(f for f in facets if not f.startswith("pattern"))
    return "; ".join(parts)


def _type_text(sp: SchemaPath) -> str:
    return f"list of {sp.item_type or 'values'}" if sp.type == "array" else sp.type


def _title(schema: dict, sp: SchemaPath) -> str:
    title = _node(schema, parse_path(sp.path)).get("title")
    return title if isinstance(title, str) else ""


def _template_columns(template: ExtractionTemplate) -> list[tuple[str, str, SchemaPath]]:
    """``(column, concrete path, schema path)`` for each value column of ``template``, in column order."""
    schema = template.schema_
    generic: dict[str, SchemaPath] = {}
    for sp in flatten_schema(schema):
        if _leaf(sp):
            for concrete in _expand(sp.path, _max_items(schema, sp.path)):
                generic[concrete] = sp
    return [(column_name(path), path, generic[path]) for path in concrete_paths(schema)]


def _columns_and_fields(
    templates: Sequence[ExtractionTemplate], doc_columns: Sequence[str] = _DOC_COLUMNS
) -> tuple[list[str], list[dict]]:
    """The value columns (``doc_columns``, then each template's) and one ``fields`` row per schema column."""
    columns = list(doc_columns)
    fields: dict[str, dict] = {}
    for template in templates:
        for column, path, sp in _template_columns(template):
            if column in doc_columns:
                raise FormcastError(f"the field {path!r} would share the document column {column}; change its title")
            if column in fields:
                # a column two templates share is described by the first, if they agree on what it is
                first = fields[column]
                if first["path"] != path or first["type"] != _type_text(sp):
                    raise FormcastError(
                        f"{template.template_id}: column {column!r} is {path!r} ({_type_text(sp)}) here but "
                        f"{first['path']!r} ({first['type']}) in an earlier template; rename the path"
                    )
                continue
            columns.append(column)
            fields[column] = {
                "column": column, "path": path, "title": _title(template.schema_, sp),
                "definition": sp.description, "type": _type_text(sp), "checks": _checks(template.schema_, sp),
            }  # fmt: skip
    return columns, list(fields.values())


def check_columns(templates: Sequence[ExtractionTemplate], *, names: bool = False) -> None:
    """Refuse ``templates`` whose export would be refused for its columns, as :func:`export` checks them: a field
    whose column is a document column (``document_name`` too when the export will have ``names``), or one column made by two
    different paths or types. A template can be checked so before a run, instead of failing at its export.

    Raises:
        FormcastError: the clash, naming the field (``"the field 'document_name' would share the document column
            document_name; change its title"``).
    """
    _columns_and_fields(templates, _named(_DOC_COLUMNS) if names else _DOC_COLUMNS)


# ---------------------------------------------------------------------- rows


def _named(columns: Sequence[str]) -> tuple[str, ...]:
    """``columns`` with ``document_name`` after ``document_id``."""
    return (columns[0], NAME_COLUMN, *columns[1:])


def _with_names(rows: Iterable[dict], names: dict[str, str]) -> Iterator[dict]:
    """Each row with its document's name (its ``doc_id`` when ``names`` has none) after ``doc_id``."""
    for row in rows:
        doc_id = row["document_id"]
        rest = {k: v for k, v in row.items() if k != "document_id"}
        yield {"document_id": doc_id, NAME_COLUMN: names.get(doc_id, doc_id), **rest}


def _in_words(rows: Iterable[dict]) -> Iterator[dict]:
    """Each ``status`` row with its fields' statuses in :data:`STATUS_WORDS`' words."""
    for row in rows:
        yield {k: v if k in _DOC_COLUMNS else STATUS_WORDS.get(v, v) for k, v in row.items()}


def _checked(rows: Iterable[dict], columns: Sequence[str], part: str) -> Iterator[dict]:
    """``rows`` as they are, each checked to have no column outside ``columns`` (a template that was not given)."""
    known = set(columns)
    for row in rows:
        extra = [name for name in row if name not in known]
        if extra:
            raise FormcastError(
                f"{part}: document {row.get('document_id')!r} has columns {', '.join(extra)} that no given template "
                "has; pass every template the run used"
            )
        yield row


def _text(value: Any) -> Any:
    """A value for a cell of text: lists and objects as JSON, everything else as it is."""
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value


# ----------------------------------------------------------------------- csv


def _write_csv(path: Path, columns: Sequence[str], rows: Iterable[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as sink:
        writer = csv.writer(sink)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(["" if row.get(c) is None else _text(row[c]) for c in columns])


# ---------------------------------------------------------------------- xlsx


def _write_xlsx(
    openpyxl: Any, path: Path, parts: dict[str, tuple[Sequence[str], Iterable[dict]]]
) -> None:
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    book = openpyxl.Workbook(write_only=True)
    for name, (columns, rows) in parts.items():
        sheet = book.create_sheet(name)

        def cell(value: Any, sheet: Any = sheet) -> Any:
            value = _text(value)
            if not isinstance(value, str):
                return value
            made = WriteOnlyCell(sheet, value=ILLEGAL_CHARACTERS_RE.sub("", value))
            made.data_type = "s"  # text stays text: "=1+1" read from a PDF is not a formula
            return made

        sheet.append(list(columns))
        for row in rows:
            sheet.append([cell(row.get(c)) for c in columns])
    book.save(path)


# ------------------------------------------------------------------- parquet


def _kind(values: list[Any]) -> str:
    kinds = {type(v) for v in values if v is not None}
    if kinds == {bool}:
        return "bool"
    ints = [v for v in values if type(v) is int]
    if kinds == {int} and all(abs(v) < _INT64 for v in ints):
        return "int"
    # ints beside decimals must survive as doubles, which hold whole numbers exactly only up to 2**53
    if kinds == {float} or (kinds == {int, float} and all(abs(v) <= _EXACT_FLOAT for v in ints)):
        return "float"
    return "string"  # also an int too big for 64 bits, and a mix of types


def _write_parquet(
    pa: Any, pq: Any, path: Path, columns: Sequence[str], rows: Iterable[dict], kinds: dict[str, str] | None = None
) -> None:
    rows = list(rows)
    arrays = []
    for name in columns:
        values = [row.get(name) for row in rows]
        kind = (kinds or {}).get(name) or _kind(values)
        if kind == "string":  # text as it is; any other value (in a column that mixes them) as JSON text
            values = [v if v is None or isinstance(v, str) else json.dumps(v, ensure_ascii=False) for v in values]
        arrays.append(pa.array(values, type={"string": pa.string(), "bool": pa.bool_(), "int": pa.int64(),
                                             "float": pa.float64()}[kind]))  # fmt: skip
    pq.write_table(pa.Table.from_arrays(arrays, names=list(columns)), path)


# -------------------------------------------------------------------- export


def export(
    results: Results,
    templates: list[ExtractionTemplate],
    out: str | Path,
    *,
    format: ExportFormat,
    names: dict[str, str] | None = None,
) -> list[Path]:
    """Write ``results`` to ``out`` (see the module): a workbook for ``"xlsx"``, a folder for ``"parquet"`` or
    ``"csv"``. Files already there are replaced. ``templates`` are the templates the run used; they give the
    column order and the field definitions. ``names`` (``doc_id`` -> the name a person knows the document by) adds
    a ``document_name`` column after ``document_id`` to ``values``, ``status`` and ``set_aside``. Returns the files written.

    Raises:
        FormcastError: ``format`` is not one of the three, the extra it needs is not installed, a template's
            column is a document column (``name`` too, with ``names``) or clashes with another template's, ``out`` is
            a file where a folder
            is needed, or a document has columns that none of ``templates`` has.
    """
    if format not in _FORMATS:
        raise FormcastError(f"unknown export format {format!r} (one of {', '.join(_FORMATS)})")
    # the extra first, so a missing one fails before anything is written
    if format == "xlsx":
        openpyxl = _need("openpyxl", "xlsx", "xlsx")
    elif format == "parquet":
        pa = _need("pyarrow", "Parquet", "parquet")
        pq = _need("pyarrow.parquet", "Parquet", "parquet")
    target = Path(out)
    doc_columns = _named(_DOC_COLUMNS) if names is not None else _DOC_COLUMNS
    columns, fields = _columns_and_fields(templates, doc_columns)
    rows, status_rows, aside = results.rows(), _in_words(results.status_rows()), results.set_aside()
    if names is not None:
        rows, status_rows, aside = (_with_names(part, names) for part in (rows, status_rows, aside))
    parts: dict[str, tuple[Sequence[str], Iterable[dict]]] = {
        "values": (columns, _checked(rows, columns, "values")),
        "status": (columns, _checked(status_rows, columns, "status")),
        "fields": (FIELD_COLUMNS, fields),
        "set_aside": (_named(SET_ASIDE_COLUMNS) if names is not None else SET_ASIDE_COLUMNS, aside),
    }
    if format == "xlsx":
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_xlsx(openpyxl, target, parts)
        return [target]
    if target.exists() and not target.is_dir():
        raise FormcastError(f"{target} is a file; {format} export writes a folder")
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in _PARTS:
        part_columns, rows = parts[name]
        path = target / f"{name}.{format}"
        if format == "csv":
            _write_csv(path, part_columns, rows)
        else:
            kinds = {"closest_score": "float"} if name == "set_aside" else None
            _write_parquet(pa, pq, path, part_columns, rows, kinds)
        written.append(path)
    return written
