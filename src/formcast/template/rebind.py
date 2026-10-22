"""``rebind``: stamp a PDF's form info onto an existing template."""

from __future__ import annotations

from pathlib import Path

from formcast.errors import FormcastError
from formcast.pagemap.build import build_form_map
from formcast.pagemap.model import FormMap
from formcast.template.model import Template, form_info_for

__all__ = ["rebind"]

SIZE_TOLERANCE = 1.0  # points


def rebind(template: Template, pdf: str | Path | bytes, *, form_map: FormMap | None = None) -> Template:
    """A copy of ``template`` carrying ``pdf``'s form info (fingerprint, page kinds, signature).

    For templates made outside the library (the browser demo records no fingerprint) and for
    templates whose PDF was re-saved. Fields and history are unchanged. ``form_map`` skips building
    the page map (pass one you already built).

    Raises ``FormcastError`` when the page count differs from the template's, or a page's size
    differs by more than 1 pt.
    """
    fm = form_map if form_map is not None else build_form_map(pdf)
    info = form_info_for(fm)
    if info.page_count != template.form.page_count:
        raise FormcastError(
            f"cannot rebind: the template has {template.form.page_count} pages, this PDF has {info.page_count}"
        )
    for k, (old, new) in enumerate(zip(template.form.page_sizes, info.page_sizes), start=1):
        if abs(old[0] - new[0]) > SIZE_TOLERANCE or abs(old[1] - new[1]) > SIZE_TOLERANCE:
            raise FormcastError(
                f"cannot rebind: page {k} is {old[0]:g}x{old[1]:g} pt in the template and "
                f"{new[0]:g}x{new[1]:g} pt in this PDF"
            )
    out = template.model_copy(deep=True)
    out.form = info
    return out
