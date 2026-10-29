"""The extraction workbench's HTTP routes and jobs, kept apart from ``demo/server/api.py``.

    add_extract_routes(api)           registers the routes on an ``Api`` (called at the end of ``Api._add_routes``)
    start_extract_job(api, kind, payload) -> {"id": ...}    starts an ``extract-setup``, ``extract-line`` or
                                      ``extract-run`` task job (``Jobs.start_task``); ``Api.create_job`` hands these
                                      kinds over after reading the request

Batches (``api.batches``, ``demo/server/extraction.py``):

    POST /api/batches                {name?} | {folder, name?}      -> Batch
    POST /api/batches/{b}/files      multipart, parts named file    -> Batch   (PDFs and .zip files)
    GET  /api/batches/{b}                                           -> Batch
    GET  /api/batches/{b}/files?offset=&limit=                      -> {total, files: [{index, name, size, group}]}
    POST /api/batches/{b}/sample     {n, how: spread|first|pick, picked?: [index]} -> {samples: [{index, name, group}]}
    GET  /api/batches/{b}/files/{i}/pages/{p}.png?dpi=              -> image/png

``{folder}`` opens a folder on the server's machine in place: 403 ``not_allowed`` when ``settings.folder_scan`` is
off, 400 ``bad_folder`` when it is not a full path to a folder holding a PDF. An upload the batch refuses (a zip too
large or damaged, too many files, a folder's batch) is 400 ``bad_upload``; its body may be up to
``settings.max_upload_bytes``, as every multipart body, so the page sends a large batch in several requests. A list
page has ``limit`` 1 to 200 (default 50); a bad ``offset`` or ``limit`` is 400 ``bad_request``. A sample that cannot
be picked is 400 ``bad_sample``, and 409 ``scanning`` while the scan runs. An unknown batch is 404 ``not_found``
"no such batch: <id>" (the batches are in memory, so after a restart every id is unknown). The responses name the
batch's files by their shown names only, never by a path on the server.

The handlers are functions over the ``Api`` they are registered on, so the managers (``demo/server/extraction.py``)
stay where the ``Api`` keeps them. This module imports ``api`` for ``ApiError``, ``Request`` and the upload helpers,
so ``api`` imports it only inside the methods that call it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from demo.server.api import DEFAULT_DPI, Api, ApiError, Request, Response, _object, _uploads, missing
from demo.server.documents import MAX_DPI, MIN_DPI, page_png
from demo.server.extraction import PAGE_DEFAULT, PAGE_LIMIT, Batch, ScanNotDone
from formcast import FormcastError

BATCH = r"/api/batches/(?P<batch>[0-9a-f]{32})"


def add_extract_routes(api: Api) -> None:
    """Register the extraction routes on ``api``: the batch routes (the setup, extraction and run routes come with their managers)."""

    def route(method: str, pattern: str, handler: Callable[[Api, Request], Any]) -> None:
        api._add(method, pattern, lambda req: handler(api, req))

    route("POST", r"/api/batches", create_batch)
    route("GET", BATCH, get_batch)
    route("POST", BATCH + r"/files", add_batch_files)
    route("GET", BATCH + r"/files", list_batch_files)
    route("POST", BATCH + r"/sample", pick_sample)
    route("GET", BATCH + r"/files/(?P<i>\d{1,7})/pages/(?P<n>\d{1,6})\.png", batch_page_image)


def start_extract_job(api: Api, kind: str, payload: dict) -> dict:
    """Start the task job ``kind`` for ``payload`` and return ``{"id": job id}``. ``api.create_job`` calls this for the extract kinds.

    Raises:
        ApiError: 501 ``not_available`` until the kind's manager exists.
    """
    raise ApiError(501, "not_available", f"{kind} is not available yet")


# ------------------------------------------------------------------ batches


def _batch(api: Api, req: Request) -> Batch:
    with missing("batch"):
        return api.batches.get(req.match["batch"])


def create_batch(api: Api, req: Request) -> dict:
    """``{name?}`` -> a new empty batch for uploads; ``{folder, name?}`` -> a batch of the PDFs in a server folder, scanning."""
    payload = _object(req)
    name = payload.get("name")
    if name is not None and not isinstance(name, str):
        raise ApiError(400, "bad_request", "name must be text")
    folder = payload.get("folder")
    if folder is None:
        return api.batches.create(name).summary()
    if not api.settings.folder_scan:
        raise ApiError(403, "not_allowed", "This server does not open folders on its machine. Upload the files instead.")
    path = Path(folder.strip()).expanduser() if isinstance(folder, str) else None
    if path is None or not path.is_absolute():
        raise ApiError(400, "bad_folder", "Send folder as the full path of a folder on the server's machine.")
    try:
        return api.batches.from_folder(path, name).summary()
    except FormcastError as exc:
        raise ApiError(400, "bad_folder", str(exc)) from None


def get_batch(api: Api, req: Request) -> dict:
    return _batch(api, req).summary()


def add_batch_files(api: Api, req: Request) -> dict:
    """Multipart ``file`` parts (PDFs and zips of PDFs) added to the batch; its scan starts again."""
    batch = _batch(api, req)
    uploads = _uploads(req)
    try:
        api.batches.add(batch, uploads)
    except FormcastError as exc:
        raise ApiError(400, "bad_upload", str(exc)) from None
    return batch.summary()


def list_batch_files(api: Api, req: Request) -> dict:
    """A page of the batch's files, in index order, each with its scan group (``None`` until scanned, or unreadable)."""
    batch = _batch(api, req)
    offset = _whole(req.query, "offset", 0, 0, None)
    limit = _whole(req.query, "limit", PAGE_DEFAULT, 1, PAGE_LIMIT)
    total, files = batch.page(offset, limit)
    return {"total": total, "files": [{"index": f.index, "name": f.name, "size": f.size, "group": batch.group_of(f.index)} for f in files]}


def pick_sample(api: Api, req: Request) -> dict:
    """``{n, how, picked?}`` -> the documents to build a template from (see ``Batches.pick``)."""
    batch = _batch(api, req)
    payload = _object(req)
    try:
        indexes = api.batches.pick(batch, payload.get("n", 5), payload.get("how", "spread"), payload.get("picked"))
    except ScanNotDone as exc:
        raise ApiError(409, "scanning", str(exc)) from None
    except FormcastError as exc:
        raise ApiError(400, "bad_sample", str(exc)) from None
    files = [batch.file(i) for i in indexes]
    return {"samples": [{"index": f.index, "name": f.name, "group": batch.group_of(f.index)} for f in files]}


def batch_page_image(api: Api, req: Request) -> Response:
    """``GET .../files/{i}/pages/{p}.png?dpi=110`` (both count from 0): a page of one of the batch's files."""
    batch = _batch(api, req)
    raw = req.query.get("dpi", str(DEFAULT_DPI))
    try:
        dpi = int(raw)
    except ValueError:
        raise ApiError(400, "bad_dpi", f"dpi must be a whole number from {MIN_DPI} to {MAX_DPI}") from None
    with missing("file"):
        f = batch.file(int(req.match["i"]))
    n = int(req.match["n"])
    try:
        data = f.path.read_bytes()
    except OSError:
        raise ApiError(404, "not_found", f"{f.name} is no longer on the server") from None
    try:
        png = page_png(data, n, dpi)
    except IndexError:
        raise ApiError(404, "not_found", f"{f.name} has no page {n + 1}") from None
    except Exception:  # MuPDF raises several types for a file it cannot open
        raise ApiError(422, "bad_pdf", f"{f.name} can't be shown: it is not a PDF that can be read.") from None
    return Response(200, {"Content-Type": "image/png", "Cache-Control": "private, max-age=3600"}, png)


def _whole(query: dict[str, str], key: str, default: int, low: int, high: int | None) -> int:
    """The query's ``key`` as a whole number from ``low`` to ``high`` (no upper bound when ``None``); 400 ``bad_request`` otherwise."""
    raw = query.get(key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = None
    if value is None or value < low or (high is not None and value > high):
        bound = f"from {low} to {high}" if high is not None else f"of {low} or more"
        raise ApiError(400, "bad_request", f"{key} must be a whole number {bound}")
    return value
