"""The workbench JSON API: a route table over ``Api.route``.

``Api.route(method, path, query, body, headers)`` returns ``(status, headers, body_bytes)`` and
never raises: every failure becomes ``{"error": "<code>", "detail": "<plain message>"}``. ``path`` is the
path as sent, still percent-encoded: routes match it as it is and each captured part (a template name, a
record id) is decoded on its own, so an id with an encoded slash (``%2F``) stays one part.

    ApiError                       the status and code the handler chose
    StoredTemplateError            422 bad_template_file   (a stored file that is not a template)
    RecordSourceError              422 bad_records         (a record file that cannot be read)
    FormcastError (library)        400 bad_input, with the library's plain message
    anything else                  500 internal; the traceback goes to the server log only

A ``KeyError`` or ``ValueError`` is NOT mapped globally: one that escapes a handler is a bug and
becomes a 500. A handler turns the ones it expects into answers where it calls the store, the
records or the documents: ``with missing("template"):`` makes a ``KeyError`` a 404, and
``with bad_request():`` makes a ``ValueError`` (a bad template name) a 400.

Routes (see spec 11.2): documents, matches, seed, apply, rebind, AI jobs, fill, smart-pdf, the template
store and the record source. The one-shot flow adds: ``POST /api/data`` (a table file or pasted text to
fill with), the ``oneshot`` job kind (``demo/server/oneshot.py``), opening a saved template's own PDF
(``POST /api/documents {"template": name}``), saving the PDF and a data mapping with a template, the
template's schema as a file, and ``POST /api/templates/{name}/fill``, the programmatic way in: a record,
records, a table file or free text in; a filled PDF (or a zip of them) out. Everything that touches MuPDF takes ``PYMUPDF_LOCK`` (directly for fills,
smart PDFs and rebinds; inside ``Doc`` for the form map and the match features). Matching takes no lock:
it scores the document's features, which ``Doc.match_features`` computes in full, so it does not wait for a build.

The PDF extraction workbench's routes and its ``extract-setup``, ``extract-line`` and ``extract-run`` jobs are in
``demo/server/extract_api.py`` (``Api.create_job`` hands those kinds over; they need no model).

Handlers are small methods registered in ``Api._add_routes``; they return a JSON-able value
(sent as 200) or a ``Response``.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import math
import re
import zipfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from typing import Any
from urllib.parse import unquote

import pydantic

from demo.server.datasets import Dataset, Datasets
from demo.server.documents import DETECT_MODES, MAX_DPI, MIN_DPI, PYMUPDF_LOCK, Doc, Documents
from demo.server.jobs import EXTRACT_KINDS, KINDS, Jobs, JobsBusy
from demo.server.oneshot import OneShotSpec, map_dataset
from demo.server.records import MultiRecords, RecordSource, RecordSourceError
from demo.server.settings import Settings
from demo.server.store import (
    NAME_RE, FolderTemplateStore, OverlayTemplateStore, ShippedTemplateError, StoredTemplateError, TemplateStore,
)
from formcast import (
    FormcastError, OpContext, OpError, Template, apply, fill, flatten_schema, rebind, similarity, to_smart_pdf,
)
from formcast.data import DataMapping
from formcast.llm import Provider
from formcast.template.model import candidate_fields, new_template

log = logging.getLogger("demo.server")

DEFAULT_DPI = 110


class ApiError(Exception):
    """A deliberate error response."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


@dataclass
class Response:
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    body: bytes
    headers: Mapping[str, str]
    match: dict[str, str]  # the route's captured parts, percent-decoded

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")

    def json(self) -> Any:
        """The body as JSON. Raises ``ApiError`` 400 ``bad_json`` if it isn't."""
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "bad_json", "the request body is not valid JSON") from None


@contextmanager
def missing(what: str) -> Iterator[None]:
    """Inside the block, a ``KeyError`` (unknown template, schema, record, document) is a 404."""
    try:
        yield
    except KeyError as exc:
        key = exc.args[0] if exc.args and isinstance(exc.args[0], str) else ""
        raise ApiError(404, "not_found", f"no such {what}" + (f": {key}" if key else "")) from None


@contextmanager
def bad_request() -> Iterator[None]:
    """Inside the block, a ``ValueError`` (e.g. a bad template name) is a 400 with its message.

    ``RecordSourceError`` is a ``ValueError`` too but keeps its own answer (422).
    """
    try:
        yield
    except RecordSourceError:
        raise
    except ValueError as exc:
        raise ApiError(400, "bad_request", str(exc)) from None


def json_response(value: Any, status: int = 200) -> Response:
    body = json.dumps(value, ensure_ascii=False).encode("utf-8")
    return Response(status, {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}, body)


def error_response(status: int, code: str, detail: str) -> Response:
    return json_response({"error": code, "detail": detail}, status)


class Api:
    def __init__(
        self,
        settings: Settings,
        *,
        provider_factory: Callable[[], Provider] | None = None,
        store: TemplateStore | None = None,
        records: RecordSource | None = None,
        documents: Documents | None = None,
    ) -> None:
        self.settings = settings
        self.provider_factory = provider_factory
        if store is None:  # saves go to <data>/local/templates (git-ignored), over the shipped <data>/templates
            local = settings.data_dir / "local" / "templates"
            store = OverlayTemplateStore(local, settings.data_dir / "templates") if settings.demo_enabled else FolderTemplateStore(local)
        self.store = store
        self.records = records if records is not None else _default_records(settings)
        self.documents = documents if documents is not None else Documents()
        self.datasets = Datasets()
        self.jobs = Jobs(secrets=[settings.llm.api_key] if settings.llm is not None else [])
        # the extraction workbench's managers (demo/server/extraction.py): batches of PDFs, setups, saved extractions, runs
        self.batches = self.setups = self.extractions = self.runs = None
        self._routes: list[tuple[str, re.Pattern[str], Callable[[Request], Any]]] = []
        self._add_routes()

    # ------------------------------------------------------------ dispatch

    def body_limit(self, method: str, path: str, content_type: str) -> int:
        """The most bytes this request may carry: uploads 50 MB by default, JSON 20 MB."""
        if content_type.lower().startswith("multipart/"):
            return self.settings.max_upload_bytes
        return self.settings.max_json_bytes

    def route(
        self,
        method: str,
        path: str,
        query: Mapping[str, str] | None = None,
        body: bytes = b"",
        headers: Mapping[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Handle one API request. ``headers`` keys are lower-case."""
        try:
            response = self._dispatch(method.upper(), path, dict(query or {}), body, headers or {})
        except ApiError as exc:
            response = error_response(exc.status, exc.code, exc.detail)
        except StoredTemplateError as exc:
            response = error_response(422, "bad_template_file", str(exc))
        except RecordSourceError as exc:
            response = error_response(422, "bad_records", str(exc))
        except FormcastError as exc:
            response = error_response(400, "bad_input", str(exc))
        except Exception:  # a bug or a failure we did not foresee: log it, tell the client nothing about it
            log.exception("unexpected error handling %s %s", method, path)
            response = error_response(500, "internal", "unexpected error; the server log has the details")
        return response.status, response.headers, response.body

    def _dispatch(self, method: str, path: str, query: dict[str, str], body: bytes, headers: Mapping[str, str]) -> Response:
        allowed: list[str] = []
        for verb, pattern, handler in self._routes:
            m = pattern.fullmatch(path)
            if m is None:
                continue
            if verb != method and not (method == "HEAD" and verb == "GET"):
                allowed.append(verb)
                continue
            parts = {name: unquote(value) for name, value in m.groupdict().items()}
            result = handler(Request(method, path, query, body, headers, parts))
            return result if isinstance(result, Response) else json_response(result)
        if allowed:
            response = error_response(405, "method_not_allowed", f"use {' or '.join(sorted(set(allowed)))} for this path")
            response.headers["Allow"] = ", ".join(sorted(set(allowed)))
            return response
        raise ApiError(404, "not_found", "no such route")

    def _add(self, method: str, pattern: str, handler: Callable[[Request], Any]) -> None:
        self._routes.append((method, re.compile(pattern), handler))

    def _add_routes(self) -> None:
        self._add("GET", r"/api/capabilities", self.capabilities)
        self._add("GET", r"/api/forms", self.forms)
        self._add("POST", r"/api/documents", self.create_document)
        self._add("GET", r"/api/documents/(?P<doc>[0-9a-f]{32})/pages/(?P<n>\d{1,6})\.png", self.page_image)
        self._add("GET", r"/api/documents/(?P<doc>[0-9a-f]{32})/matches", self.matches)
        self._add("GET", r"/api/documents/(?P<doc>[0-9a-f]{32})/progress", self.document_progress)
        self._add("POST", r"/api/documents/(?P<doc>[0-9a-f]{32})/pages/(?P<n>\d{1,6})/detect", self.detect_page)
        self._add("POST", r"/api/seed", self.seed)
        self._add("POST", r"/api/apply", self.apply_op)
        self._add("POST", r"/api/rebind", self.rebind_template)
        self._add("POST", r"/api/jobs", self.create_job)
        self._add("GET", r"/api/jobs/(?P<job>[0-9a-f]{32})", self.get_job)
        self._add("POST", r"/api/jobs/(?P<job>[0-9a-f]{32})/cancel", self.cancel_job)
        self._add("POST", r"/api/fill", self.fill_pdf)
        self._add("POST", r"/api/smart-pdf", self.smart_pdf)
        self._add("POST", r"/api/data", self.create_dataset)
        self._add("GET", r"/api/data/(?P<data>[0-9a-f]{32})", self.get_dataset)
        self._add("GET", r"/api/templates", self.list_templates)
        self._add("GET", r"/api/templates/(?P<name>[^/]+)/schema", self.template_schema)
        self._add("GET", r"/api/templates/(?P<name>[^/]+)/mappings", self.template_mappings)
        self._add("POST", r"/api/templates/(?P<name>[^/]+)/fill", self.fill_from_template)
        self._add("GET", r"/api/templates/(?P<name>.+)", self.get_template)
        self._add("PUT", r"/api/templates/(?P<name>.+)", self.put_template)
        self._add("DELETE", r"/api/templates/(?P<name>.+)", self.delete_template)
        self._add("GET", r"/api/schemas", self.schemas)
        self._add("GET", r"/api/records", self.list_records)
        self._add("GET", r"/api/records/(?P<schema>[^/]+)/(?P<id>[^/]+)", self.get_record)
        from demo.server.extract_api import add_extract_routes  # here, not at the top: extract_api imports this module

        add_extract_routes(self)

    # ------------------------------------------------------------ routes

    def capabilities(self, req: Request) -> dict:
        """Engine flags. Says only whether AI is configured, never how."""
        return {
            "ai": self.provider_factory is not None,
            "matches": True,
            "store": True,
            "records": True,
            "scanned": True,
            "detectLive": False,
            "oneshot": True,
            "xlsx": _has_openpyxl(),
            "progress": True,
            "detectPage": True,
            "extract": True,
        }

    def _forms_dir(self):
        return self.settings.data_dir / "forms"

    def _template_pdf(self, name: str) -> bytes:
        """The PDF kept with a saved template, or the demo form of the same name. ``KeyError`` when neither exists."""
        get_file = getattr(self.store, "get_file", None)
        if get_file is not None:
            try:
                return get_file(name, "pdf")
            except KeyError:
                pass
        if self.settings.demo_enabled and NAME_RE.fullmatch(name) and (path := self._forms_dir() / f"{name}.pdf").is_file():
            self.store.get(name)  # only for a stored template (KeyError otherwise)
            return path.read_bytes()
        raise KeyError(name)

    def forms(self, req: Request) -> dict:
        """The demo forms in ``<data>/forms``, each with its schema name when one has the same name; none with the demo off."""
        out = []
        if not self.settings.demo_enabled:
            return {"forms": out}
        for path in sorted(self._forms_dir().glob("*.pdf")):
            if NAME_RE.fullmatch(path.stem):
                has_schema = (self.settings.data_dir / "schemas" / f"{path.stem}.json").is_file()
                out.append({"name": path.stem, "size": path.stat().st_size, "schema": path.stem if has_schema else None})
        return {"forms": out}

    def create_document(self, req: Request) -> dict:
        """Open an uploaded PDF (multipart ``file``), a demo form (JSON ``{"form": name}``) or the PDF a saved
        template keeps (JSON ``{"template": name}``). ``?detect=manual`` reads it with no detection: the person
        picks the pages to search (``POST .../pages/{n}/detect``)."""
        detect = req.query.get("detect", "auto")
        if detect not in DETECT_MODES:
            raise ApiError(400, "bad_detect", f"detect must be one of {', '.join(DETECT_MODES)}")
        if req.content_type.lower().startswith("multipart/"):
            name, data = _upload(req)
        elif isinstance(payload := req.json(), dict) and payload.get("template") is not None:
            name = payload["template"]
            with missing("template"), bad_request():
                data = self._template_pdf(name)
            name = f"{name}.pdf"
        else:
            form = payload.get("form") if isinstance(payload, dict) else None
            if not isinstance(form, str) or not NAME_RE.fullmatch(form):
                raise ApiError(400, "bad_form", 'send a plain demo form name as {"form": "<name>"}, or upload a file')
            if not self.settings.demo_enabled:
                raise ApiError(404, "not_found", "the demo forms are off; upload a PDF or open a saved template")
            path = self._forms_dir() / f"{form}.pdf"
            if not path.is_file():
                raise ApiError(404, "not_found", f"no such demo form: {form}")
            name, data = f"{form}.pdf", path.read_bytes()
        try:
            doc = self.documents.add(data, name, detect=detect)
        except FormcastError as exc:
            raise ApiError(400, "bad_pdf", f"This file can't be used: {exc}.") from None
        return doc.summary()

    def page_image(self, req: Request) -> Response:
        """``GET /api/documents/{id}/pages/{n}.png?dpi=110`` (n counts from 0). Does not run detection."""
        with missing("document"):
            doc = self.documents.get(req.match["doc"])
        raw = req.query.get("dpi", str(DEFAULT_DPI))
        try:
            dpi = int(raw)
        except ValueError:
            raise ApiError(400, "bad_dpi", f"dpi must be a whole number from {MIN_DPI} to {MAX_DPI}") from None
        try:
            png = doc.page_png(int(req.match["n"]), dpi)
        except IndexError:
            raise ApiError(404, "not_found", f"this document has {doc.page_count} pages") from None
        return Response(200, {"Content-Type": "image/png", "Cache-Control": "private, max-age=3600"}, png)

    def document_progress(self, req: Request) -> dict:
        """``GET /api/documents/{id}/progress``: how reading the form is going (``Doc.progress``). Never waits."""
        with missing("document"):
            doc = self.documents.get(req.match["doc"])
        return doc.progress()

    def detect_page(self, req: Request) -> dict:
        """``POST /api/documents/{id}/pages/{n}/detect`` -> ``{page, detected, fields, boxes}``: detection on page n alone.

        ``fields`` are the new unmapped fields for the boxes found (as the seed makes them), for the page to
        add to its template; empty when the page was searched already (``detected`` false) or is fillable.
        """
        with missing("document"):
            doc = self.documents.get(req.match["doc"])
        n = int(req.match["n"])
        try:
            page, ran = doc.detect_page(n)
        except IndexError:
            raise ApiError(404, "not_found", f"this document has {doc.page_count} pages") from None
        fields = [f.model_dump(by_alias=True, mode="json") for f in candidate_fields(page)] if ran else []
        return {"page": n, "detected": ran, "fields": fields, "boxes": len(page.candidates)}

    # ---- templates: seed, edit, rebind, match

    def seed(self, req: Request) -> dict:
        """``{doc, schema}`` -> ``{template}``: an empty template with a field per widget and per detector candidate.

        ``schema`` is a JSON Schema object, or the name of a schema the record source has.
        """
        payload = _object(req)
        doc = self._doc(payload)
        schema = self._schema(payload)
        form_map = doc.form_map()  # takes the MuPDF lock itself
        return {"template": _dump(new_template(schema, form_map, candidates=True))}

    def _schema(self, payload: dict) -> dict:
        """``payload["schema"]``: a JSON Schema object, or the name of a schema the record source has."""
        schema = payload.get("schema")
        if isinstance(schema, str):
            with missing("schema"):
                schema = self.records.schemas()[schema]
        if not isinstance(schema, dict):
            raise ApiError(400, "bad_schema", "send schema as a JSON Schema object or the name of a stored schema")
        if _schema_loops(schema):
            raise ApiError(400, "bad_schema", _SCHEMA_LOOP)
        flatten_schema(schema)  # a schema the library cannot use is a 400 now (FormcastError), not a failed job later
        return schema

    def apply_op(self, req: Request) -> dict:
        """``{template, op, author, note?, doc?}`` -> ``{template, warnings}``; a rejected op is 422 ``op_rejected``.

        With ``doc`` and a template made for that PDF, widget links are checked against the PDF's
        widget names and kinds; otherwise only against the template's own schema and page sizes.
        """
        payload = _object(req)
        template = _template(payload)
        op = payload.get("op")
        if op is None:
            raise ApiError(400, "bad_request", "send the edit as op")
        author = payload.get("author")
        if author not in ("human", "agent"):
            raise ApiError(400, "bad_author", 'author must be "human" or "agent"')
        note = payload.get("note", "")
        if not isinstance(note, str):
            raise ApiError(400, "bad_request", "note must be text")
        context = None
        if payload.get("doc") is not None:
            doc = self._doc(payload)
            if doc.has_fingerprint(template.form.fingerprint):
                try:
                    context = OpContext.from_template(template, doc.form_map())
                except Exception:  # e.g. the detection weights cannot be fetched: check against the template alone
                    log.warning("apply: could not build the form map; checking the edit against the template only", exc_info=True)
        try:
            updated, warnings = apply(template, op, author=author, note=note, context=context)
        except OpError as exc:
            raise ApiError(422, "op_rejected", exc.reason) from None
        except RecursionError:
            raise ApiError(422, "bad_schema", _SCHEMA_LOOP) from None
        return {"template": _dump(updated), "warnings": warnings}

    def rebind_template(self, req: Request) -> dict:
        """``{template, doc}`` -> ``{template}`` carrying the PDF's fingerprint, page kinds and signature."""
        payload = _object(req)
        template = _template(payload)
        doc = self._doc(payload)
        form_map = doc.form_map()
        try:
            with PYMUPDF_LOCK:
                rebound = rebind(template, doc.bytes, form_map=form_map)
        except FormcastError as exc:
            raise ApiError(422, "cannot_rebind", str(exc)) from None
        return {"template": _dump(rebound)}

    def matches(self, req: Request) -> list[dict]:
        """Every stored template scored against this document, best first, each with a suggested action.

        The PDF is read once (``Doc.match_features``) and the same features score every template. The features
        are complete, so scoring does no MuPDF work and does not wait for a form-map build holding
        ``PYMUPDF_LOCK``. A stored file that is not a template is left out and logged.
        """
        with missing("document"):
            doc = self.documents.get(req.match["doc"])
        features = doc.match_features()
        templates: list[tuple[str, Template]] = []
        for entry in self.store.list():
            try:
                templates.append((entry["name"], self.store.get(entry["name"])))
            except KeyError:  # deleted since the listing
                continue
            except StoredTemplateError as exc:
                log.warning("matches: skipping %s", exc)
        out = []
        scored = [(name, template, similarity(template, doc.bytes, features=features)) for name, template in templates]
        for name, template, result in scored:
            out.append({
                "name": name,
                "template_id": template.template_id,
                "score": result.score,
                "basis": result.basis,
                "explanation": result.explanation,
                "action": self._action(result.score),
            })  # fmt: skip
        out.sort(key=lambda m: (-m["score"], m["name"]))
        return out

    def _action(self, score: float) -> str:
        if score >= self.settings.reuse_threshold:
            return "reuse"
        return "adapt" if score >= self.settings.adapt_threshold else "new"

    # ---- AI jobs

    def create_job(self, req: Request) -> dict:
        """``{kind, doc, schema?, template?, instruction?, region?}`` -> ``{id}``; poll ``GET /api/jobs/{id}``.

        ``build`` takes ``schema`` (an object or a stored schema's name). ``adapt`` takes ``template`` and
        uses its own schema. For ``adapt`` and ``revise`` the template must have as many pages as the PDF
        (400 ``page_count_mismatch``). ``revise`` takes ``template`` and ``instruction``, and optionally
        ``region: {page, rect: [x0, y0, x1, y1]}`` in PDF points (origin top-left). A job runs on a thread,
        two at a time; a third is refused with 429 ``busy``. Every input is checked here, before a thread starts.
        """
        payload = _object(req)
        kind = payload.get("kind")
        if kind == "oneshot":
            return self._start_oneshot(payload)
        if kind in EXTRACT_KINDS:  # these need no model; the extraction workbench starts them
            from demo.server.extract_api import start_extract_job  # here, not at the top: extract_api imports this module

            return start_extract_job(self, kind, payload)
        if kind not in KINDS:
            raise ApiError(400, "bad_kind", f"kind must be {', '.join((*KINDS, *EXTRACT_KINDS)[:-1])} or {EXTRACT_KINDS[-1]}")
        if self.provider_factory is None:
            raise ApiError(400, "ai_not_configured", "AI is not configured. Set the model endpoint, key and name in .env (see the README).")
        doc = self._doc(payload)
        schema = template = instruction = region = None
        if kind == "build":
            schema = self._schema(payload)
        else:
            template = _template(payload)
            if template.form.page_count != doc.page_count:  # adapt moves boxes between pages of other sizes, never other pages
                raise ApiError(
                    400, "page_count_mismatch",
                    f"This template is for a form with {_pages(template.form.page_count)} and this PDF has "
                    f"{_pages(doc.page_count)}, so it can't be {'adapted to' if kind == 'adapt' else 'revised on'} it.",
                )  # fmt: skip
            if _schema_loops(template.schema_):
                raise ApiError(400, "bad_schema", _SCHEMA_LOOP)
            flatten_schema(template.schema_)
        if kind == "revise":
            instruction = payload.get("instruction")
            if not isinstance(instruction, str) or not instruction.strip():
                raise ApiError(400, "bad_instruction", "send instruction as the text of the change to make")
            region = _region(payload.get("region"), doc)
        try:
            job = self.jobs.start(
                kind, doc=doc, schema=schema, template=template, instruction=instruction, region=region,
                provider_factory=self.provider_factory,
            )  # fmt: skip
        except JobsBusy as exc:
            raise ApiError(429, "busy", str(exc)) from None
        return {"id": job.id}

    def _start_oneshot(self, payload: dict) -> dict:
        """``{kind: "oneshot", doc, data?, schema?, template?, saved?, check?, instructions?}`` -> ``{id}``.

        With no ``schema`` and no ``template`` the model designs the schema from the form. ``template`` (an
        object) skips the schema and boxes: only the data is mapped. ``saved`` names a stored template whose
        saved data mappings are tried before the model. ``check`` (default true) has the agent check the
        page pass. Without AI, only a template with data whose columns it knows can run.
        """
        doc = self._doc(payload)
        spec = OneShotSpec(check=payload.get("check", True) is not False)
        if payload.get("template") is not None:
            spec.template = _template(payload)
            if spec.template.form.page_count != doc.page_count:
                raise ApiError(400, "page_count_mismatch", f"This template is for a form with {_pages(spec.template.form.page_count)} and this PDF has {_pages(doc.page_count)}.")
            if _schema_loops(spec.template.schema_):
                raise ApiError(400, "bad_schema", _SCHEMA_LOOP)
            flatten_schema(spec.template.schema_)
        elif payload.get("schema") is not None:
            spec.schema = self._schema(payload)
        if payload.get("data") is not None:
            spec.dataset = self._dataset(payload["data"])
        saved = payload.get("saved")
        if saved is not None:
            if not isinstance(saved, str):
                raise ApiError(400, "bad_request", "saved must be a stored template's name")
            spec.saved_mappings = self._mappings(saved)
        instructions = payload.get("instructions", "")
        if not isinstance(instructions, str):
            raise ApiError(400, "bad_request", "instructions must be text")
        spec.instructions = instructions[:2000]
        needs_ai = spec.template is None or (spec.dataset is not None and spec.dataset.kind == "text")
        if needs_ai and self.provider_factory is None:
            raise ApiError(400, "ai_not_configured", "AI is off, so a form can't be mapped or text read. Set the model endpoint, key and name in .env (see the README), or open a saved template and give it a table.")
        try:
            job = self.jobs.start("oneshot", doc=doc, oneshot=spec, provider_factory=self.provider_factory)
        except JobsBusy as exc:
            raise ApiError(429, "busy", str(exc)) from None
        return {"id": job.id}

    def get_job(self, req: Request) -> dict:
        """Status, the steps so far and, once it has ended, the template (see ``demo/server/jobs.py``)."""
        with missing("job"):
            return self.jobs.get(req.match["job"]).snapshot()

    def cancel_job(self, req: Request) -> dict:
        """Ask the job to stop at its next step; returns its state now (still ``running`` until it stops)."""
        with missing("job"):
            return self.jobs.cancel(req.match["job"]).snapshot()

    # ---- fill

    def fill_pdf(self, req: Request) -> dict:
        """``{template, doc, record?, record_ref?: {schema, id}}`` -> ``{pdf_base64, report}`` (plus ``fingerprint_note``)."""
        payload = _object(req)
        template = _template(payload)
        doc = self._doc(payload)
        data = self._record(payload)
        check, note = self._fingerprint_gate(template, doc, payload)
        try:
            with PYMUPDF_LOCK:
                pdf, report = fill(template, doc.bytes, data, check_fingerprint=check)
        except RecursionError:
            raise ApiError(422, "bad_schema", _SCHEMA_LOOP) from None
        out: dict[str, Any] = {"pdf_base64": base64.b64encode(pdf).decode("ascii"), "report": report.model_dump(mode="json")}
        if note:
            out["fingerprint_note"] = note
        return out

    def smart_pdf(self, req: Request) -> dict:
        """``{template, doc, check_fingerprint?}`` -> ``{pdf_base64}``: the PDF with an empty field for every template field it lacks.

        The fingerprint rule is the same as for ``fill``.
        """
        payload = _object(req)
        template = _template(payload)
        doc = self._doc(payload)
        check, note = self._fingerprint_gate(template, doc, payload)
        with PYMUPDF_LOCK:
            pdf = to_smart_pdf(template, doc.bytes, check_fingerprint=check)
        out: dict[str, Any] = {"pdf_base64": base64.b64encode(pdf).decode("ascii")}
        if note:
            out["fingerprint_note"] = note
        return out

    def _fingerprint_gate(self, template: Template, doc: Doc, payload: dict) -> tuple[bool, str | None]:
        """Whether to check the fingerprint, and the note to send back when the check is skipped.

        Equal fingerprints: checked, no note. A template with none (browser or old): unchecked, with
        a note. A different one: 409 ``fingerprint_mismatch`` unless the body says
        ``"check_fingerprint": false``, which fills anyway, with a note.
        """
        override = payload.get("check_fingerprint")
        if override is not None and not isinstance(override, bool):
            raise ApiError(400, "bad_request", "check_fingerprint must be true or false")
        expected = template.form.fingerprint
        if expected is None:
            return False, _fingerprint_note(template)
        if doc.has_fingerprint(expected):
            return True, None
        if override is False:
            return False, _fingerprint_note(template)
        raise ApiError(409, "fingerprint_mismatch", "This template was made for a different version of this form. Rebind it to this PDF first.")

    def _record(self, payload: dict) -> dict:
        """The record to fill with: ``record`` as sent, or ``record_ref`` read from the record source."""
        record, ref = payload.get("record"), payload.get("record_ref")
        if record is not None and ref is not None:
            raise ApiError(400, "bad_request", "send record or record_ref, not both")
        if ref is not None:
            if not (isinstance(ref, dict) and isinstance(ref.get("schema"), str) and isinstance(ref.get("id"), str)):
                raise ApiError(400, "bad_request", 'record_ref must look like {"schema": "<name>", "id": "<id>"}')
            with missing("record"):
                return self.records.get(ref["schema"], ref["id"])
        if record is None:
            return {}
        if not isinstance(record, dict):
            raise ApiError(400, "bad_request", "record must be a JSON object")
        return record

    def _doc(self, payload: dict) -> Doc:
        doc_id = payload.get("doc")
        if not isinstance(doc_id, str):
            raise ApiError(400, "bad_request", "send doc as the document id")
        with missing("document"):
            return self.documents.get(doc_id)

    # ---- template store

    def list_templates(self, req: Request) -> dict:
        out = self.store.list()
        for t in out:
            if self.settings.demo_enabled and not t.get("has_pdf") and (self._forms_dir() / f"{t['name']}.pdf").is_file():
                t["has_pdf"] = True  # a shipped template opens with the demo form of its name
        return {"templates": out}

    def get_template(self, req: Request) -> dict:
        with missing("template"), bad_request():
            return {"template": _dump(self.store.get(req.match["name"]))}

    def put_template(self, req: Request) -> dict:
        """Save the body's ``template`` under this name (replacing any with that name).

        With ``doc``, the store also keeps that PDF, so the saved template opens without it; with ``mapping``
        (a data mapping), the store keeps it among the template's mappings (one per set of columns), so a
        table with those columns fills without the model next time.
        """
        payload = _object(req)
        template = _template(payload)
        name = req.match["name"]
        doc = self._doc(payload) if payload.get("doc") is not None else None
        mapping = None
        if payload.get("mapping") is not None:
            try:
                mapping = DataMapping.model_validate(payload["mapping"])
            except pydantic.ValidationError as exc:
                raise ApiError(400, "bad_mapping", f"this is not a data mapping: {exc.errors()[0]['msg']}") from None
        if (doc is not None or mapping is not None) and not hasattr(self.store, "put_file"):
            raise ApiError(400, "not_supported", "this template store keeps templates only")
        with bad_request():
            self.store.put(name, template)
            if doc is not None:
                self.store.put_file(name, "pdf", doc.bytes)
            if mapping is not None:
                kept = [m for m in self._mappings(name) if set(m.columns) != set(mapping.columns)]
                body = {"mappings": [m.dump() for m in [mapping, *kept][:20]]}
                self.store.put_file(name, "mappings", json.dumps(body, indent=1).encode("utf-8"))
        return {"name": name, "template_id": template.template_id, "pdf": doc is not None, "mapping": mapping is not None}

    def _mappings(self, name: str) -> list[DataMapping]:
        """The data mappings saved with a template ([] when none, or the store keeps none)."""
        get_file = getattr(self.store, "get_file", None)
        if get_file is None:
            return []
        try:
            with bad_request():
                raw = json.loads(get_file(name, "mappings"))
        except KeyError:
            return []
        except (ValueError, UnicodeDecodeError):
            log.warning("the data mappings saved with %s can't be read; ignoring them", name)
            return []
        out = []
        for item in raw.get("mappings", []) if isinstance(raw, dict) else []:
            try:
                out.append(DataMapping.model_validate(item))
            except pydantic.ValidationError:
                continue
        return out

    def template_schema(self, req: Request) -> Response:
        """The saved template's data schema as a JSON file: what records for it must look like."""
        name = req.match["name"]
        with missing("template"), bad_request():
            template = self.store.get(name)
        body = json.dumps(template.schema_, indent=2, ensure_ascii=False).encode("utf-8")
        return Response(200, {
            "Content-Type": "application/schema+json; charset=utf-8",
            "Content-Disposition": f'attachment; filename="{_safe_filename(name)}.schema.json"',
            "Cache-Control": "no-store",
        }, body)  # fmt: skip

    def template_mappings(self, req: Request) -> dict:
        name = req.match["name"]
        with missing("template"), bad_request():
            self.store.get(name)
        return {"name": name, "mappings": [m.dump() for m in self._mappings(name)]}

    def fill_from_template(self, req: Request) -> Response:
        """Fill a saved template's own PDF, with no browser: the programmatic way in.

        The body is JSON ``{"record": {...}}``, ``{"records": [...]}`` or ``{"text": "..."}`` (free text,
        read by the model), or a multipart ``file`` (CSV, TSV, XLSX or JSON records; the columns are mapped
        directly, by a saved mapping, or by the model, in that order). One record answers ``application/pdf``;
        several answer ``application/zip`` with one PDF per record and ``reports.json``. Each response
        carries ``X-Formcast-Records`` and ``X-Formcast-Warnings`` (counts).
        """
        name = req.match["name"]
        with missing("template"), bad_request():
            template = self.store.get(name)
            pdf = self._template_pdf(name)
        schema = template.schema_
        if req.content_type.lower().startswith("multipart/"):
            filename, data = _upload(req)
            try:
                dataset = self.datasets.add_file(data, filename)
            except FormcastError as exc:
                raise ApiError(400, "bad_data", f"This file can't be used: {exc}.") from None
            records = self._records_for(schema, dataset, name)
        else:
            payload = _object(req)
            if isinstance(payload.get("record"), dict):
                records = [payload["record"]]
            elif isinstance(payload.get("records"), list) and all(isinstance(r, dict) for r in payload["records"]):
                records = payload["records"]
            elif isinstance(payload.get("text"), str) and payload["text"].strip():
                records = self._records_for(schema, self.datasets.add_text(payload["text"]), name)
            else:
                raise ApiError(400, "bad_request", 'send {"record": {...}}, {"records": [...]}, {"text": "..."} or a file')
        if not records:
            raise ApiError(422, "no_records", "no records were found in that data")
        filled = []
        try:
            for record in records:
                with PYMUPDF_LOCK:
                    filled.append(fill(template, pdf, record, check_fingerprint=template.form.fingerprint is not None))
        except RecursionError:
            raise ApiError(422, "bad_schema", _SCHEMA_LOOP) from None
        warnings = sum(1 for _, rep in filled for w in rep.warnings if w.severity == "warning")
        headers = {"X-Formcast-Records": str(len(filled)), "X-Formcast-Warnings": str(warnings), "Cache-Control": "no-store"}
        stem = _safe_filename(name)
        if len(filled) == 1:
            headers |= {"Content-Type": "application/pdf", "Content-Disposition": f'attachment; filename="{stem}-filled.pdf"'}
            return Response(200, headers, filled[0][0])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            width = len(str(len(filled)))
            for i, (out, _) in enumerate(filled, 1):
                z.writestr(f"{stem}-{i:0{width}d}.pdf", out)
            z.writestr("reports.json", json.dumps([rep.model_dump(mode="json") for _, rep in filled], indent=1))
        headers |= {"Content-Type": "application/zip", "Content-Disposition": f'attachment; filename="{stem}-filled.zip"'}
        return Response(200, headers, buf.getvalue())

    def _records_for(self, schema: dict, dataset: Dataset, name: str) -> list[dict]:
        provider = self.provider_factory() if self.provider_factory is not None else None
        try:
            return map_dataset(schema, dataset, provider, self._mappings(name)).records
        except FormcastError as exc:
            raise ApiError(422, "cannot_map", str(exc)) from None
        finally:
            if provider is not None:
                provider.close()

    # ---- data to fill with

    def create_dataset(self, req: Request) -> dict:
        """A table file (multipart ``file``: CSV, TSV, XLSX, JSON) or pasted text (JSON ``{"text": ...}``) -> its summary.

        Pasted text that is JSON records or a delimited table is read as a table; anything else is free text.
        """
        try:
            if req.content_type.lower().startswith("multipart/"):
                name, data = _upload(req)
                ds = self.datasets.add_file(data, name)
            else:
                text = _object(req).get("text")
                if not isinstance(text, str) or not text.strip():
                    raise ApiError(400, "bad_request", 'send a file, or {"text": "..."}')
                ds = self.datasets.add_text(text)
        except FormcastError as exc:
            raise ApiError(400, "bad_data", f"This data can't be used: {exc}.") from None
        return ds.summary()

    def get_dataset(self, req: Request) -> dict:
        with missing("data"):
            return self.datasets.get(req.match["data"]).summary()

    def _dataset(self, ds_id: Any) -> Dataset:
        if not isinstance(ds_id, str):
            raise ApiError(400, "bad_request", "send data as the id POST /api/data returned")
        with missing("data"):
            return self.datasets.get(ds_id)

    def delete_template(self, req: Request) -> dict:
        """Delete a saved template; one that ships with the demo is 409 ``read_only``."""
        try:
            with missing("template"), bad_request():
                self.store.delete(req.match["name"])
        except ShippedTemplateError as exc:
            raise ApiError(409, "read_only", str(exc)) from None
        return {"deleted": req.match["name"]}

    # ---- schemas and records

    def schemas(self, req: Request) -> dict:
        return {"schemas": self.records.schemas()}

    def list_records(self, req: Request) -> dict:
        schema = req.query.get("schema")
        if not schema:
            raise ApiError(400, "bad_request", "send ?schema=<name>")
        with missing("schema"):
            return {"schema": schema, "records": self.records.list(schema)}

    def get_record(self, req: Request) -> dict:
        with missing("record"):
            return {"record": self.records.get(req.match["schema"], req.match["id"])}


_SCHEMA_LOOP = "This schema refers to itself in a loop ($ref), so it can't be used. Fix the schema and try again."
_SAME_PLACE = ("allOf", "anyOf", "oneOf")  # keywords whose subschemas apply to the same piece of data


def _schema_loops(schema: dict) -> bool:
    """Whether a local ``$ref`` chain in ``schema`` leads back to itself without consuming any data.

    Checking data against such a schema never ends (a ``RecursionError`` in fill). Recursion through
    ``properties`` or ``items`` (a tree) is fine, because each step consumes a level of the data.
    """

    def resolve(ref: str):
        if not ref.startswith("#"):
            return None
        node: Any = schema
        for part in [p for p in ref[1:].split("/") if p]:
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                return None
        return node

    def walk(node: Any, trail: tuple[str, ...]) -> bool:
        """Follow what applies to the same data from ``node``; ``trail`` holds the refs on the way."""
        if not isinstance(node, dict):
            return False
        ref = node.get("$ref")
        if isinstance(ref, str):
            if ref in trail:
                return True
            if walk(resolve(ref), (*trail, ref)):
                return True
        for key in _SAME_PLACE:
            subs = node.get(key)
            if isinstance(subs, list) and any(walk(sub, trail) for sub in subs):
                return True
        return any(walk(node.get(key), trail) for key in ("not", "if", "then", "else"))

    def every(node: Any) -> bool:
        if isinstance(node, dict):
            return walk(node, ()) or any(every(v) for v in node.values())
        if isinstance(node, list):
            return any(every(v) for v in node)
        return False

    return every(schema)


def _object(req: Request) -> dict:
    """The JSON body, which must be an object."""
    payload = req.json()
    if not isinstance(payload, dict):
        raise ApiError(400, "bad_json", "send a JSON object")
    return payload


def _template(payload: dict) -> Template:
    """``payload["template"]`` as a ``Template``. A template that doesn't validate is a 400 naming the first problem."""
    raw = payload.get("template")
    if not isinstance(raw, dict):
        raise ApiError(400, "bad_template", "send template as a JSON object")
    try:
        return Template.model_validate(raw)
    except pydantic.ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise ApiError(400, "bad_template", f"this is not a valid template: {where or 'template'}: {first['msg']}") from None


def _region(raw: Any, doc: Doc) -> tuple[int, tuple[float, float, float, float]] | None:
    """``{page, rect}`` from a request as ``(page, rect)``; ``None`` when absent. A bad one is a 400."""
    if raw is None:
        return None
    shape = 'region must look like {"page": 0, "rect": [x0, y0, x1, y1]} (PDF points)'
    if not isinstance(raw, dict):
        raise ApiError(400, "bad_region", shape)
    page, rect = raw.get("page"), raw.get("rect")
    if isinstance(page, bool) or not isinstance(page, int):
        raise ApiError(400, "bad_region", shape)
    if not 0 <= page < doc.page_count:
        raise ApiError(400, "bad_region", f"region page must be from 0 to {doc.page_count - 1} (this document has {doc.page_count} pages)")
    if not (isinstance(rect, list) and len(rect) == 4 and all(type(v) in (int, float) for v in rect)):
        raise ApiError(400, "bad_region", shape)
    try:
        x0, y0, x1, y1 = (float(v) for v in rect)  # OverflowError: an absurd integer
    except OverflowError:
        raise ApiError(400, "bad_region", shape) from None
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        raise ApiError(400, "bad_region", shape)
    return page, (x0, y0, x1, y1)


def _pages(n: int) -> str:
    return f"{n} page" if n == 1 else f"{n} pages"


def _dump(template: Template) -> dict:
    return template.model_dump(by_alias=True, mode="json")


def _fingerprint_note(template: Template) -> str:
    if template.form.fingerprint is None:
        return (
            "This template has no fingerprint (it was made in the browser or before fingerprints existed), so the "
            "check that it was made for this PDF was skipped. Rebind it to this PDF to record one."
        )
    return (
        "This PDF is not the exact form the template was built for (its fingerprint differs), so the check was "
        "skipped and the PDF was filled anyway. Rebind the template to this PDF if it is a new revision."
    )


def _default_records(settings: Settings) -> RecordSource:
    """The demo's schemas and records; with the demo off, none (fills use the person's own data)."""
    from demo.server.records import CsvRecords, SqliteRecords

    if not settings.demo_enabled:
        return MultiRecords()
    data = settings.data_dir
    return MultiRecords(CsvRecords(data), SqliteRecords(data / "records.sqlite", data / "schemas"))


def _has_openpyxl() -> bool:
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        return False
    return True


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or "template"


def _uploads(req: Request) -> list[tuple[str, bytes]]:
    """Every ``file`` part of a multipart body as ``(display name, bytes)``, in order. Raises ``ApiError`` 400 ``bad_upload`` when there is none."""
    head = b"Content-Type: " + req.content_type.encode("latin-1", "replace") + b"\r\nMIME-Version: 1.0\r\n\r\n"
    try:
        message = BytesParser(policy=policy.HTTP).parsebytes(head + req.body)
        parts = list(message.iter_parts()) if message.is_multipart() else []
    except Exception:
        parts = []
    files = [
        (_display_name(part.get_filename()), part.get_payload(decode=True) or b"")
        for part in parts
        if part.get_param("name", header="content-disposition") == "file"
    ]
    if not files:
        raise ApiError(400, "bad_upload", "send the file as a multipart form field named file")
    return files


def _upload(req: Request) -> tuple[str, bytes]:
    """The first ``file`` part of a multipart body as ``(display name, bytes)``."""
    return _uploads(req)[0]


def _display_name(filename: str | None) -> str:
    """A label for the UI. Only ever shown, never used as a path, but keep it tidy anyway."""
    base = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(c for c in base if c.isprintable()).strip()
    return base[:120] or "document.pdf"
