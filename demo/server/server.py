"""The workbench HTTP server: ``demo/web/`` as static files and ``/api/*`` through ``Api.route``.

Standard library only. It is a ``ThreadingHTTPServer``: every request runs in its own thread,
so handlers share nothing except the ``Api`` object, whose parts (documents, jobs) take locks.
Run it on your own machine only.

Browser-facing guards, because this server lives on localhost and any web page you have open can
send it requests: a request whose ``Host`` is not this server's own address is refused (DNS
rebinding), a state-changing ``/api`` request from another site is refused (``Origin``), pages
cannot be framed, dot-files are never served, and any request whose body was not read closes the
connection, so leftover bytes can never be parsed as a second request.
"""

from __future__ import annotations

import logging
import mimetypes
import sys
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlsplit

from demo.server.api import Api, error_response
from demo.server.records import RecordSource
from demo.server.settings import ROOT, Settings
from demo.server.store import TemplateStore
from formcast.llm import Provider, make_provider

log = logging.getLogger("demo.server")

WEB_ROOT = ROOT / "demo" / "web"
LANDING_PAGES = ("/workbench/", "/showcase/")  # where "/" goes when demo/web has no index.html

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".wasm": "application/wasm",
    ".svg": "image/svg+xml",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".onnx": "application/octet-stream",
}
REQUEST_TIMEOUT = 30  # seconds a connection may sit idle or stall mid-request
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
}
_DRAIN_LIMIT = 64 * 2**20  # an over-limit body is read and thrown away up to this much, so the client gets the 413


def _size(n: int) -> str:
    return f"{n // 2**20} MB" if n >= 2**20 else f"{n} byte"


def content_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    return CONTENT_TYPES.get(suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def resolve_static(web_root: Path, url_path: str) -> Path | None:
    """The file under ``web_root`` for a URL path, or ``None`` for anything that could leave it."""
    decoded = unquote(url_path)
    if "\x00" in decoded or "\\" in decoded:
        return None
    parts = [p for p in decoded.split("/") if p not in ("", ".")]
    if any(p.startswith(".") for p in parts):  # "..", .git, .env: never served
        return None
    root = web_root.resolve()
    target = root.joinpath(*parts).resolve()
    if target != root and root not in target.parents:  # a symlink that points outside
        return None
    return target


def allowed_hosts(bind_host: str, port: int) -> set[str]:
    """The ``Host`` header values this server answers to: loopback names plus the bind address."""
    names = {"127.0.0.1", "localhost", "[::1]"}
    if bind_host not in ("0.0.0.0", "::", ""):
        names.add(f"[{bind_host}]" if ":" in bind_host else bind_host)
    return {f"{name}:{port}" for name in names} | ({*names} if port == 80 else set())


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        """Log a failed request through ``logging``; a client that went away is not worth a traceback."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            log.info("client %s went away: %s", client_address[0], type(exc).__name__)
        else:
            log.error("error handling a request from %s", client_address[0], exc_info=True)


def make_server(
    settings: Settings,
    *,
    provider_factory: Callable[[], Provider] | None = None,
    store: TemplateStore | None = None,
    records: RecordSource | None = None,
    web_root: Path | None = None,
) -> ThreadingHTTPServer:
    """A server bound to ``settings.host:settings.port`` (port 0 picks a free one); call ``serve_forever``.

    ``provider_factory`` builds the model provider for AI jobs. With none given, one is built from
    ``settings.llm`` when that is set; with neither, the AI features are off. ``store`` and
    ``records`` default to the folder store and CSV + SQLite sources under ``settings.data_dir``.
    The server's ``api`` attribute is the ``Api`` it routes to.
    """
    if provider_factory is None and settings.llm is not None:
        llm = settings.llm
        provider_factory = lambda: make_provider(llm)  # noqa: E731
    api = Api(settings, provider_factory=provider_factory, store=store, records=records)
    root = Path(web_root) if web_root is not None else WEB_ROOT

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "formcast-workbench"
        sys_version = ""
        timeout = REQUEST_TIMEOUT

        # -------------------------------------------------- plumbing

        def log_message(self, format: str, *args) -> None:  # noqa: A002 - signature fixed by the base class
            log.info("%s %s", self.address_string(), format % args)

        def _send(self, status: int, headers: dict[str, str], body: bytes) -> None:
            head_only = self.command == "HEAD"
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def _send_error(self, status: int, code: str, detail: str, extra: dict[str, str] | None = None) -> None:
            r = error_response(status, code, detail)
            self._send(r.status, {**r.headers, **(extra or {})}, r.body)

        def _host_ok(self) -> bool:
            """A rebinding page reaches us under its own name; only our own address is accepted."""
            host = (self.headers.get("Host") or "").strip().lower()
            return host in allowed_hosts(settings.host.lower(), self.server.server_address[1])

        def _same_origin(self) -> bool:
            """Browsers send ``Origin`` on cross-site requests; refuse one from another site."""
            origin = self.headers.get("Origin")
            return origin is None or urlsplit(origin).netloc == self.headers.get("Host")

        def _read_body(self, path: str, content_type: str) -> bytes | None:
            """The request body, or ``None`` after answering 411/413/400."""
            if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
                self.close_connection = True
                self._send_error(411, "length_required", "send a Content-Length header")
                return None
            raw = self.headers.get("Content-Length", "0").strip() or "0"
            try:
                length = int(raw)
                if length < 0:
                    raise ValueError
            except ValueError:
                self.close_connection = True
                self._send_error(400, "bad_request", "bad Content-Length")
                return None
            limit = api.body_limit(self.command, path, content_type)
            if length > limit:
                # Read and drop the body so the client can read the answer, then close.
                remaining = min(length, _DRAIN_LIMIT)
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 1 << 20))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                self.close_connection = True
                self._send_error(413, "too_large", f"This request is larger than the {_size(limit)} limit.")
                return None
            return self.rfile.read(length) if length else b""

        # -------------------------------------------------- dispatch

        def _handle(self) -> None:
            parts = urlsplit(self.path)
            is_api = parts.path == "/api" or parts.path.startswith("/api/")
            reads_body = is_api and self.command in ("POST", "PUT", "PATCH", "DELETE")
            has_body = self.headers.get("Content-Length", "0").strip() not in ("", "0") or "Transfer-Encoding" in self.headers
            if has_body and not reads_body:
                self.close_connection = True  # an unread body would be parsed as the next request
            if len(self.headers.get_all("Content-Length") or []) > 1:
                # Two lengths can be read two ways by a proxy and by us: refuse rather than guess.
                self.close_connection = True
                self._send_error(400, "bad_request", "send one Content-Length header")
            elif not self._host_ok():
                self.close_connection = True
                self._send_error(403, "bad_host", "this server answers only at its own address (127.0.0.1 or localhost)")
            elif is_api:
                self._handle_api(parts.path, parts.query)
            elif self.command in ("GET", "HEAD"):
                self._handle_static(parts.path)
            else:
                self.close_connection = True
                self._send_error(405, "method_not_allowed", "this path only serves GET", {"Allow": "GET, HEAD"})

        def _handle_api(self, path: str, query: str) -> None:
            if self.command not in ("GET", "HEAD") and not self._same_origin():
                self.close_connection = True
                self._send_error(403, "cross_origin", "requests from other sites are refused")
                return
            content_type = self.headers.get("Content-Type", "")
            body = b""
            if self.command in ("POST", "PUT", "PATCH", "DELETE"):
                body = self._read_body(path, content_type)
                if body is None:
                    return
            headers = {k.lower(): v for k, v in self.headers.items()}
            status, out_headers, out = api.route(self.command, unquote(path), dict(parse_qsl(query)), body, headers)
            self._send(status, out_headers, out)

        def _handle_static(self, url_path: str) -> None:
            target = resolve_static(root, url_path)
            if target is None:
                self._send_error(404, "not_found", "not found")
                return
            if target.is_dir():
                if not url_path.endswith("/"):  # relative links in the page need the slash
                    here = quote(target.relative_to(root.resolve()).as_posix())
                    self._send(301, {"Location": f"/{here}/"}, b"")
                    return
                index = target / "index.html"
                if index.is_file():
                    target = index
                elif target == root.resolve():
                    for page in LANDING_PAGES:
                        if (root / page.strip("/") / "index.html").is_file():
                            self._send(302, {"Location": page}, b"")
                            return
                    self._send_error(404, "not_found", "not found")
                    return
                else:
                    self._send_error(404, "not_found", "not found")  # no directory listings
                    return
            try:
                data = target.read_bytes()
            except OSError:
                self._send_error(404, "not_found", "not found")
                return
            self._send(200, {"Content-Type": content_type_for(target), "Cache-Control": "no-cache"}, data)

        def _run(self) -> None:
            try:
                self._handle()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True  # the client went away
            except TimeoutError:
                # A client that stalled mid-body (REQUEST_TIMEOUT): nothing to answer, nothing to log loudly.
                self.close_connection = True
                log.info("client %s stalled while sending a request; closed", self.address_string())
            except Exception:
                log.exception("unexpected error serving %s", self.path)
                self.close_connection = True
                try:
                    self._send_error(500, "internal", "unexpected error; the server log has the details")
                except OSError:
                    pass

        do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = _run

    httpd = _Server((settings.host, settings.port), Handler)
    httpd.api = api  # type: ignore[attr-defined]
    return httpd
