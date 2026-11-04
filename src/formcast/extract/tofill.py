"""A fill template for a blank copy of a form, made from the extraction template that reads its filled copies.

    fill_template_for(extraction, form_map, sample=None) -> (Template, warnings)

An extraction template knows, for every schema path, which form field holds it: a widget read names the field (and
its page, for a form that reuses a name on several pages), a choice names the button of each answer. Those are the
fill's targets too, so a blank copy of the same form is mapped by code, with no model call: each widget of the blank
copy (``new_template``'s widget fields, ids ``w<page>_<index>``) gets the source of the path read from it.

    widget read, one name      the name's widgets (on the read's page, or on every page no other path's read of the
                               name claims) take the path; a date gets ``date_format`` in the format the sample
                               wrote it in
    widget read, several names the parts of one answer: a date's month, day and year each take the path with
                               ``date_format`` of its part of the format; text running on over several lines takes
                               a ``slice`` of it each, cut where the sample broke it
    choice of form fields      each answer's button (by its on-state, on the mark's page) takes the path with
                               ``when`` equal to the answer; a yes/no path's one-option choice is the lone box,
                               on for its answer
    box, label or phrase read  nothing: a blank fillable copy has no text there to replace; the path is listed in a
                               warning

``sample`` is a filled copy the template reads (one of its samples): the raw text read there tells each date's
printed format. Without one, a date's format comes from the reader's own ``date`` cleaning step, else ISO.
A widget two paths would fill keeps the first path and the second is warned of. A widget no read names, of a name
read on another page, that the sample fills with the value of a path reading that name, repeats that path (a later
page repeating the first page's answer); with a sample, a name read unpaged by several paths goes, page by page, to
the path whose value the sample writes there. Paths of repeated groups are
concrete (``dependents[1].relationship``); the fill looks them up by index.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from formcast.data.mapping import _DATE_FORMATS
from formcast.extract.model import ChoiceRead, ExtractionTemplate, WidgetRead, concrete_paths
from formcast.extract.page import DocText
from formcast.pagemap.model import FormMap
from formcast.schema import flatten_schema, is_array_item, parse_path
from formcast.template.model import Field, Source, Template, new_template
from formcast.transforms import Transform

__all__ = ["fill_template_for"]

#: How small a filled value's font may get to fit its box: a form's own boxes size their text to fit, often below the
#: fill's usual floor, and a filled copy should show what the filled original showed.
MIN_FONT_SIZE = 4


def fill_template_for(
    extraction: ExtractionTemplate, form_map: FormMap, sample: bytes | None = None
) -> tuple[Template, list[str]]:
    """The fill template for the blank copy whose form map is ``form_map``, and warnings for what it left unmapped."""
    from formcast.extract.read import extract  # not at the top: read imports much of the package

    schema = extraction.schema_
    template = new_template(schema, form_map)
    types = {sp.path: sp for sp in flatten_schema(schema)}
    raws: dict[str, str] = {}
    shown: dict[tuple[str, int], str] = {}  # (form field name, page) -> what the sample's widget there holds
    if sample is not None:
        raws = {f.path: f.raw for f in extract(extraction, sample).fields if f.raw}
        with DocText(sample) as text:
            for page in form_map.pages:
                for w in page.widgets:
                    if w.kind == "text":
                        value = text.widget_value(w.name, page.index)
                        shown[w.name, page.index] = value if isinstance(value, str) else ""
    states = {f"w{p.index}_{i}": w.state for p in form_map.pages for i, w in enumerate(p.widgets)}
    by_name: dict[str, list[Field]] = {}
    for f in template.fields:
        if f.existing_widget is not None:
            by_name.setdefault(f.existing_widget, []).append(f)

    claimed = _claimed_pages(extraction)
    sources: dict[str, Source] = {}
    owner: dict[str, str] = {}
    warnings: list[str] = []

    def give(fid: str, path: str, source: Source) -> None:
        if fid in sources:
            if owner[fid] != path:
                warnings.append(f"{path}: widget {fid} is filled from {owner[fid]} already; left to it")
            return
        sources[fid], owner[fid] = source, path

    readers_of = _readers_of(extraction)

    def widgets(name: str, page: int | None, path: str) -> list[Field]:
        on = by_name.get(name, [])
        if page is not None and (sample is None or _differs(shown, name)):
            return [f for f in on if f.page == page]  # kept to its page: the name holds other answers elsewhere
        taken = {p for other, pages in claimed.get(name, {}).items() if other != path for p in pages}
        return [f for f in on if f.page not in taken and _shows(f, path)]

    def _shows(f: Field, path: str) -> bool:
        """Whether ``path`` is the one the sample's widget ``f`` holds the value of, among the paths that read its
        name: a name reused on several pages for different answers goes, page by page, to the path whose value the
        sample writes there (an empty widget to a path with no value). With no sample, or no path matching, any."""
        here = shown.get((f.existing_widget, f.page))  # type: ignore[arg-type]
        others = readers_of.get(f.existing_widget, [])  # type: ignore[arg-type]
        if here is None or len(others) < 2:
            return True
        matching = [p for p in others if _same(raws.get(p), here)]
        return not matching or path in matching

    for path in concrete_paths(schema):
        reader = extraction.readers.get(path)
        if reader is None or path in extraction.absent:
            continue
        sp = types.get(_generic(path))
        is_date = (_node(schema, _generic(path)) or {}).get("format") == "date"
        yes_no = sp is not None and sp.type == "boolean"
        formats = _formats(reader.clean) if is_date else []

        def map_choice(choice: ChoiceRead, path: str = path, yes_no: bool = yes_no) -> bool:
            placed = False
            for key, mark in choice.options.items():
                if mark.widget is None:
                    continue
                answer = _answer(key, yes_no)
                lone = yes_no and len(choice.options) == 1
                for f in widgets(mark.widget, mark.page, path):
                    if mark.state is not None and states.get(f.id) not in (None, mark.state) and len(by_name[mark.widget]) > 1:
                        continue
                    when = None if lone and answer is True else Transform(op="equals", args={"value": answer})
                    give(f.id, path, Source(paths=[path], when=when))
                    placed = True
            return placed

        def map_read(read: WidgetRead, raw: str | None, path: str = path, is_date: bool = is_date) -> bool:
            placed = False
            if len(read.names) == 1:
                fmt = _date_format(raw, formats) if is_date else None
                transforms = [Transform(op="date_format", args={"format": fmt})] if fmt else []
                for f in widgets(read.names[0], read.page, path):
                    give(f.id, path, Source(paths=[path], transforms=transforms))
                    placed = True
            elif is_date:
                parts = _date_parts(raw, formats, len(read.names))
                if parts is None:
                    warnings.append(f"{path}: a date split over {', '.join(read.names)} in an unknown format; left unmapped")
                else:
                    for name, part in zip(read.names, parts, strict=True):
                        for f in widgets(name, read.page, path):
                            give(f.id, path, Source(paths=[path], transforms=[Transform(op="date_format", args={"format": part})]))
                            placed = True
            else:
                cuts = _cuts([_shown_on(shown, name, read.page) for name in read.names])
                if cuts is None:
                    warnings.append(f"{path}: split over {', '.join(read.names)} with no sample to show where; left unmapped")
                else:
                    for name, args in zip(read.names, cuts, strict=True):
                        for f in widgets(name, read.page, path):
                            give(f.id, path, Source(paths=[path], transforms=[Transform(op="slice", args=args), Transform(op="strip")]))
                            placed = True
            return placed

        choice = next((s for s in reader.strategies if isinstance(s, ChoiceRead) and _of_widgets(s)), None)
        read = next((s for s in reader.strategies if isinstance(s, WidgetRead)), None)
        placed = map_choice(choice) if choice is not None else map_read(read, raws.get(path)) if read is not None else False
        # the field's other places: each form field it is written in again, filled as the place shows it on the sample
        for place in reader.places:
            if isinstance(place, ChoiceRead) and _of_widgets(place):
                placed = map_choice(place) or placed
            elif isinstance(place, WidgetRead):
                shows = [_shown_on(shown, name, place.page) for name in place.names]
                raw = " ".join(v for v in shows if v) or raws.get(path)
                placed = map_read(place, raw) or placed
        if not placed and reader.strategies:
            warnings.append(f"{path}: read from no form field of this copy, so the fill leaves it out")

    # A widget no path reads, of a name some path reads on another page, that the sample fills with that path's value
    # (a later page repeating the first page's answer): it repeats that path.
    for f in template.fields:
        name = f.existing_widget
        if f.id in sources or name is None or not shown.get((name, f.page)):  # type: ignore[arg-type]
            continue
        match = next((p for p in readers_of.get(name, []) if _same(raws.get(p), shown[name, f.page])), None)  # type: ignore[index]
        twin = next((g for g in by_name[name] if g.id in sources and owner[g.id] == match), None)
        if match is not None and twin is not None:
            give(f.id, match, sources[twin.id])

    fields = [
        f.model_copy(update={"source": sources[f.id], "style": f.style.model_copy(update={"min_font_size": MIN_FONT_SIZE})})
        if f.id in sources else f
        for f in template.fields
    ]  # fmt: skip
    for f in fields:
        if f.source is not None and f.source.when is not None and f.type == "text":
            f.type = "checkbox"  # a choice's button seeded as text by a form with an odd widget kind
    return template.model_copy(update={"fields": fields}), warnings


def _generic(path: str) -> str:
    return re.sub(r"\[\d+\]", "[]", path)


def _node(schema: dict, path: str) -> dict | None:
    """The schema node at the generic ``path``, through ``properties`` and ``items``; ``None`` when there is none."""
    node: Any = schema
    for seg in parse_path(path):
        node = node.get("items") if is_array_item(seg) else (node.get("properties") or {}).get(seg)
        if not isinstance(node, dict):
            return None
    return node


def _differs(shown: dict[tuple[str, int], str], name: str) -> bool:
    """Whether the sample's widgets named ``name`` hold different answers on different pages."""
    return len({v for (n, _), v in shown.items() if n == name}) > 1


def _shown_on(shown: dict[tuple[str, int], str], name: str, page: int | None) -> str | None:
    """What the sample's widget ``name`` holds (on ``page``, else on the first page where it holds anything)."""
    if page is not None:
        return shown.get((name, page))
    return next((v for (n, _), v in shown.items() if n == name and v), None)


def _cuts(parts: list[str | None]) -> list[dict] | None:
    """For text split over several form fields (an answer running on to a second line), each field's ``slice`` of the
    joined value: where the sample broke it, the last field taking the rest. ``None`` without the sample's parts."""
    if not parts or any(not p for p in parts):
        return None
    out, start = [], 0
    for i, part in enumerate(parts):
        end = start + len(part.strip())
        out.append({"start": start} if i == len(parts) - 1 else {"start": start, "end": end})
        start = end + 1  # the space the parts are joined with
    return out


def _same(raw: str | None, shown: str) -> bool:
    return " ".join((raw or "").split()).casefold() == " ".join(shown.split()).casefold()


def _readers_of(extraction: ExtractionTemplate) -> dict[str, list[str]]:
    """Form field name -> the paths whose widget reads name it, in path order."""
    out: dict[str, list[str]] = {}
    for path, reader in extraction.readers.items():
        for s in [*reader.strategies, *reader.places]:
            if isinstance(s, WidgetRead) and len(s.names) == 1:
                out.setdefault(s.names[0], []).append(path)
    return out


def _claimed_pages(extraction: ExtractionTemplate) -> dict[str, dict[str, set[int]]]:
    """Form field name -> path -> the pages that path's page-bound reads of the name read it on."""
    out: dict[str, dict[str, set[int]]] = {}
    for path, reader in extraction.readers.items():
        for s in [*reader.strategies, *reader.places]:
            if isinstance(s, WidgetRead) and s.page is not None:
                for name in s.names:
                    out.setdefault(name, {}).setdefault(path, set()).add(s.page)
            if isinstance(s, ChoiceRead):
                for mark in s.options.values():
                    if mark.widget is not None and mark.page is not None:
                        out.setdefault(mark.widget, {}).setdefault(path, set()).add(mark.page)
    return out


def _of_widgets(choice: ChoiceRead) -> bool:
    return any(mark.widget is not None for mark in choice.options.values())


def _answer(key: str, yes_no: bool) -> Any:
    """A choice key as the record holds it: ``True`` / ``False`` for a yes/no path, else the key."""
    if yes_no and key.strip().lower() in ("true", "yes"):
        return True
    if yes_no and key.strip().lower() in ("false", "no"):
        return False
    return key


def _formats(clean: list[Transform]) -> list[str]:
    """The strptime formats of the reader's ``date`` cleaning steps, in order."""
    return [f for t in clean if t.op == "date" for f in (t.args.get("formats") or [])]


def _date_format(raw: str | None, formats: list[str]) -> str | None:
    """The format ``raw`` is written in: the first of ``formats`` then the common ones that reads it; else the first
    of ``formats``; ``None`` (ISO, as the record holds it) when there is neither."""
    if raw:
        for fmt in [*formats, *_DATE_FORMATS]:
            try:
                datetime.strptime(raw.strip(), fmt)
            except ValueError:
                continue
            return fmt
    return formats[0] if formats else None


def _date_parts(raw: str | None, formats: list[str], count: int) -> list[str] | None:
    """The format of each of a split date's ``count`` parts, from the format its joined parts are read with."""
    fmt = _date_format(raw, formats) or (formats[0] if formats else None)
    parts = fmt.split() if fmt else []
    return parts if len(parts) == count else None
