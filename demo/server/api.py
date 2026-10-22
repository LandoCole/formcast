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
store and the record source. Everything that touches MuPDF takes ``PYMUPDF_LOCK`` (directly for fills,
smart PDFs and rebinds; inside ``Doc`` for the form map and the match features).

Handlers are small methods registered in ``Api._add_routes``; they return a JSON-able value
(sent as 200) or a ``Response``.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from typing import Any
from urllib.parse import unquote

import pydantic

from demo.server.documents import MAX_DPI, MIN_DPI, PYMUPDF_LOCK, Doc, Documents
from demo.server.jobs import KINDS, Jobs, JobsBusy
from demo.server.records import MultiRecords, RecordSource, RecordSourceError
from demo.server.settings import Settings
from demo.server.store import NAME_RE, OverlayTemplateStore, ShippedTemplateError, StoredTemplateError, TemplateStore
from formcast import (
    FormcastError, OpContext, OpError, Template, apply, fill, flatten_schema, rebind, similarity, to_smart_pdf,
)
from formcast.llm import Provider
from formcast.template.model import new_template

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
            store = OverlayTemplateStore(settings.data_dir / "local" / "templates", settings.data_dir / "templates")
        self.store = store
        self.records = records if records is not None else _default_records(settings)
        self.documents = documents if documents is not None else Documents()
        self.jobs = Jobs(secrets=[settings.llm.api_key] if settings.llm is not None else [])
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
        self._add("POST", r"/api/seed", self.seed)
        self._add("POST", r"/api/apply", self.apply_op)
        self._add("POST", r"/api/rebind", self.rebind_template)
        self._add("POST", r"/api/jobs", self.create_job)
        self._add("GET", r"/api/jobs/(?P<job>[0-9a-f]{32})", self.get_job)
        self._add("POST", r"/api/jobs/(?P<job>[0-9a-f]{32})/cancel", self.cancel_job)
        self._add("POST", r"/api/fill", self.fill_pdf)
        self._add("POST", r"/api/smart-pdf", self.smart_pdf)
        self._add("GET", r"/api/templates", self.list_templates)
        self._add("GET", r"/api/templates/(?P<name>.+)", self.get_template)
        self._add("PUT", r"/api/templates/(?P<name>.+)", self.put_template)
        self._add("DELETE", r"/api/templates/(?P<name>.+)", self.delete_template)
        self._add("GET", r"/api/schemas", self.schemas)
        self._add("GET", r"/api/records", self.list_records)
        self._add("GET", r"/api/records/(?P<schema>[^/]+)/(?P<id>[^/]+)", self.get_record)

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
        }

    def _forms_dir(self):
        return self.settings.data_dir / "forms"

    def forms(self, req: Request) -> dict:
        """The demo forms in ``<data>/forms``, each with its schema name when one has the same name."""
        out = []
        for path in sorted(self._forms_dir().glob("*.pdf")):
            if NAME_RE.fullmatch(path.stem):
                has_schema = (self.settings.data_dir / "schemas" / f"{path.stem}.json").is_file()
                out.append({"name": path.stem, "size": path.stat().st_size, "schema": path.stem if has_schema else None})
        return {"forms": out}

    def create_document(self, req: Request) -> dict:
        """Open an uploaded PDF (multipart ``file``) or a demo form (JSON ``{"form": name}``)."""
        if req.content_type.lower().startswith("multipart/"):
            name, data = _upload(req)
        else:
            payload = req.json()
            form = payload.get("form") if isinstance(payload, dict) else None
            if not isinstance(form, str) or not NAME_RE.fullmatch(form):
                raise ApiError(400, "bad_form", 'send a plain demo form name as {"form": "<name>"}, or upload a file')
            path = self._forms_dir() / f"{form}.pdf"
            if not path.is_file():
                raise ApiError(404, "not_found", f"no such demo form: {form}")
            name, data = f"{form}.pdf", path.read_bytes()
        try:
            doc = self.documents.add(data, name)
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

        The PDF is read once (``Doc.features``) and the same features score every template. A stored
        file that is not a template is left out and logged.
        """
        with missing("document"):
            doc = self.documents.get(req.match["doc"])
        features = doc.features()
        templates: list[tuple[str, Template]] = []
        for entry in self.store.list():
            try:
                templates.append((entry["name"], self.store.get(entry["name"])))
            except KeyError:  # deleted since the listing
                continue
            except StoredTemplateError as exc:
                log.warning("matches: skipping %s", exc)
        out = []
        with PYMUPDF_LOCK:  # the features compute their parts (renders) on first use
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
        if self.provider_factory is None:
            raise ApiError(400, "ai_not_configured", "AI is not configured. Set the model endpoint, key and name in .env (see the README).")
        payload = _object(req)
        kind = payload.get("kind")
        if kind not in KINDS:
            raise ApiError(400, "bad_kind", "kind must be build, adapt or revise")
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
        return {"templates": self.store.list()}

    def get_template(self, req: Request) -> dict:
        with missing("template"), bad_request():
            return {"template": _dump(self.store.get(req.match["name"]))}

    def put_template(self, req: Request) -> dict:
        """Save the body's ``template`` under this name (replacing any with that name)."""
        template = _template(_object(req))
        with bad_request():
            self.store.put(req.match["name"], template)
        return {"name": req.match["name"], "template_id": template.template_id}

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
    from demo.server.records import CsvRecords, SqliteRecords

    data = settings.data_dir
    return MultiRecords(CsvRecords(data), SqliteRecords(data / "records.sqlite", data / "schemas"))


def _upload(req: Request) -> tuple[str, bytes]:
    """The ``file`` part of a multipart body as ``(display name, bytes)``."""
    head = b"Content-Type: " + req.content_type.encode("latin-1", "replace") + b"\r\nMIME-Version: 1.0\r\n\r\n"
    try:
        message = BytesParser(policy=policy.HTTP).parsebytes(head + req.body)
        parts = list(message.iter_parts()) if message.is_multipart() else []
    except Exception:
        parts = []
    for part in parts:
        if part.get_param("name", header="content-disposition") == "file":
            data = part.get_payload(decode=True) or b""
            return _display_name(part.get_filename()), data
    raise ApiError(400, "bad_upload", "send the PDF as a multipart form field named file")


def _display_name(filename: str | None) -> str:
    """A label for the UI. Only ever shown, never used as a path, but keep it tidy anyway."""
    base = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(c for c in base if c.isprintable()).strip()
    return base[:120] or "document.pdf"
