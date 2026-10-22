"""The workbench JSON API: a route table over ``Api.route``.

``Api.route(method, path, query, body, headers)`` returns ``(status, headers, body_bytes)`` and
never raises: every failure becomes ``{"error": "<code>", "detail": "<plain message>"}``.

    ApiError                       the status and code the handler chose
    KeyError                       404 not_found
    StoredTemplateError            422 bad_template_file   (a stored file that is not a template)
    RecordSourceError              422 bad_records         (a record file that cannot be read)
    FormcastError (library)        400 bad_input, with the library's plain message
    ValueError (e.g. bad name)     400 bad_request, with its message
    anything else                  500 internal; the traceback goes to the server log only

Handlers are small methods registered in ``Api._add_routes``; they return a JSON-able value
(sent as 200) or a ``Response``.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from typing import Any

from demo.server.documents import MAX_DPI, MIN_DPI, Documents
from demo.server.records import MultiRecords, RecordSource, RecordSourceError
from demo.server.settings import Settings
from demo.server.store import NAME_RE, FolderTemplateStore, StoredTemplateError, TemplateStore
from formcast import FormcastError
from formcast.llm import Provider

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
    match: re.Match[str]

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")

    def json(self) -> Any:
        """The body as JSON. Raises ``ApiError`` 400 ``bad_json`` if it isn't."""
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "bad_json", "the request body is not valid JSON") from None


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
        self.store = store if store is not None else FolderTemplateStore(settings.data_dir / "templates")
        self.records = records if records is not None else _default_records(settings)
        self.documents = documents if documents is not None else Documents()
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
        except KeyError as exc:
            key = exc.args[0] if exc.args and isinstance(exc.args[0], str) else ""
            response = error_response(404, "not_found", f"not found: {key}" if key else "not found")
        except FormcastError as exc:
            response = error_response(400, "bad_input", str(exc))
        except ValueError as exc:
            response = error_response(400, "bad_request", str(exc))
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
            result = handler(Request(method, path, query, body, headers, m))
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
                raise KeyError(form)
            name, data = f"{form}.pdf", path.read_bytes()
        try:
            doc = self.documents.add(data, name)
        except FormcastError as exc:
            raise ApiError(400, "bad_pdf", f"This file can't be used: {exc}.") from None
        return doc.summary()

    def page_image(self, req: Request) -> Response:
        """``GET /api/documents/{id}/pages/{n}.png?dpi=110`` (n counts from 0). Does not run detection."""
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
