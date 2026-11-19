"""A local web server (standard library only) for the one page and its small JSON API.

    GET  /                       the page
    GET  /api/state              the detector and every document with its pages
    POST /api/upload?name=&skip_prose=0|1   body: the PDF's bytes; queues it
    GET  /api/docs/<id>/download the converted PDF
    GET  /api/download-all       every converted PDF in one zip
    POST /api/reset              forget everything

It listens on 127.0.0.1 only.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from formcast_lite.jobs import Queue

STATIC = Path(__file__).resolve().parent / "static"
MAX_UPLOAD = 1024 * 1024 * 1024  # 1 GB per file
_DOWNLOAD = re.compile(r"^/api/docs/([0-9]+)/download$")


def _disposition(filename: str) -> str:
    ascii_name = filename.encode("ascii", "replace").decode().replace('"', "'").replace("?", "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def make_handler(queue: Queue):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FormCastLite"
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):  # quiet console
            pass

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj) -> None:
            self._send(status, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/api/state":
                return self._json(200, queue.state())
            if path == "/api/download-all":
                body = queue.zip_done()
                if body is None:
                    return self._json(404, {"error": "nothing converted yet"})
                return self._send(200, body, "application/zip", {"Content-Disposition": _disposition("fillable-pdfs.zip")})
            m = _DOWNLOAD.match(path)
            if m:
                doc = queue.get(m.group(1))
                if doc is None or doc.output is None:
                    return self._json(404, {"error": "not converted"})
                return self._send(200, doc.output, "application/pdf", {"Content-Disposition": _disposition(doc.out_name)})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            url = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            if url.path == "/api/reset":
                self.rfile.read(length)
                queue.reset()
                return self._json(200, {"ok": True})
            if url.path == "/api/upload":
                if length <= 0 or length > MAX_UPLOAD:
                    self.close_connection = True
                    return self._json(413 if length else 400, {"error": "empty or too large"})
                data = self.rfile.read(length)
                q = parse_qs(url.query)
                name = Path((q.get("name") or ["document.pdf"])[0]).name
                skip = (q.get("skip_prose") or ["0"])[0] == "1"
                doc = queue.add(name, data, skip)
                return self._json(200, doc.summary())
            self.rfile.read(length)
            self._json(404, {"error": "not found"})

    return Handler


def serve(queue: Queue, host: str = "127.0.0.1", port: int = 8790) -> ThreadingHTTPServer:
    """Bind (``port``, or any free port when it is taken) and serve on a background thread."""
    handler = make_handler(queue)
    try:
        httpd = ThreadingHTTPServer((host, port), handler)
    except OSError:
        httpd = ThreadingHTTPServer((host, 0), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True, name="http").start()
    return httpd
