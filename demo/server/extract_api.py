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

Setups (``api.setups``) and saved extraction templates (``api.extractions``):

    POST /api/jobs {kind: "extract-setup", batch, samples: [index], spec}   -> {id}; done: result {setup: Setup}
    POST /api/jobs {kind: "extract-line", setup, line}                      -> {id}; done: result {setup: Setup, paths}
    GET  /api/setups/{s}                                            -> Setup
    POST /api/setups                 {extraction: name}             -> Setup   (a saved template reopened)
    POST /api/setups/{s}/edit        {edit: Edit}                   -> Setup
    POST /api/setups/{s}/learn       {path}                         -> Setup
    GET  /api/setups/{s}/samples/{i}/marks                          -> {marks: [Mark]}
    GET  /api/setups/{s}/samples/{i}/pages/{p}.png?dpi=             -> image/png
    GET  /api/extractions                                           -> {extractions: [{name, template_id, fields, samples, updated}]}
    GET  /api/extractions/{name}                                    -> {template}
    PUT  /api/extractions/{name}     {setup}                        -> {name, template_id}
    DELETE /api/extractions/{name}                                  -> {name}

    Setup = {id, batch, template, proof, warnings, samples: [{index, name, kind, pages: [[w, h]]}]}

An ``extract-setup`` job builds a template from the batch's files ``samples`` (``build_setup``; at least 3 of them,
or every file of a smaller batch, and at most 20). ``spec`` is ``SetupSpec``'s fields, with ``destination`` the name
of a fill template in ``api.store`` whose schema the fields start from. Its stages are ``STAGES``, the ``fields``
stage labelled by where the fields start (``FIELDS_LABELS``); a cancel takes effect when the stage or page being read
ends. An ``extract-line`` job adds the fields of one more line to a setup (``add_line``); its result's ``paths`` are
the paths it added. The model is called by these two jobs only: an edit (``formcast.extract.edit``'s JSON edits), a
wording learned (the proof's suggestions for one path, ``FieldProof.suggested``) and a reopened template are proven
again with no model call. Marks and page images are of the setup's sample ``i`` (from 0, in the template's order);
a mark is in that sample page's frame, so a box drawn on it is sent back as a ``set_box`` edit naming the sample.

``PUT /api/extractions/{name}`` saves the setup's template under ``name`` (its ``name`` field becomes ``name``, its
``template_id`` is kept) with the setup's sample PDFs; ``POST /api/setups {extraction}`` reads those samples again
(no field detection) and proves the template on them.

Errors: no model configured, 400 ``ai_not_configured``; a bad ``spec`` 400 ``bad_spec``; a batch the server does
not have open 400 ``bad_batch``; samples that are not files of the batch 400 ``bad_sample``; an unknown destination
404 ``not_found``; a second job while ``max_running`` run 429 ``busy``. An edit the library refuses is 400
``bad_edit`` with its message; a path with no wording suggested 400 ``bad_request``. While an ``extract-line`` job
runs on a setup, an edit, a wording or another line job is 409 ``busy_setup``. An unknown setup is 404 ``not_found``
"no such setup: <id>" (setups are in memory: after a restart every id is unknown), an unknown saved template 404
"no such extraction: <name>"; a name ``store.check_name`` refuses is 400 ``bad_name``, a saved file that is not a
valid template 422 ``bad_template_file``.

Locks: these handlers and jobs hold no MuPDF lock of their own. ``formcast.extract`` takes ``PDF_LOCK`` (which is
``PYMUPDF_LOCK``) for each piece of MuPDF work it does and lets go after it: a sample's form map while it is built
(one sample at a time), a sample's blanks while they are detected, each ``DocText`` open, read and close, each
``PdfFeatures`` part (fingerprints, sizes, signature) while it is computed, each page image it renders for the model.
It never holds the lock across a model call, so page images, marks, edits and scans asked for while a job runs wait
for one such piece, not the job. When the server warms the field detectors up (``python -m demo.server``), the build
uses the same detectors as the documents, and the ``detect`` stage waits for their weights before the library takes
the lock to detect; without a warm-up the library loads its own.

The handlers are functions over the ``Api`` they are registered on, so the managers (``demo/server/extraction.py``)
stay where the ``Api`` keeps them. This module imports ``api`` for ``ApiError``, ``Request`` and the upload helpers,
so ``api`` imports it only inside the methods that call it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pydantic

from demo.server.api import AI_NOT_CONFIGURED, DEFAULT_DPI, Api, ApiError, Request, Response, _object, _uploads, missing
from demo.server.documents import MAX_DPI, MIN_DPI, page_png
from demo.server.extraction import MIN_SAMPLE, PAGE_DEFAULT, PAGE_LIMIT, Batch, ScanNotDone, Setup, SetupBusy
from demo.server.jobs import Job, JobsBusy
from demo.server.store import check_name
from formcast import FormcastError, SessionCancelled
from formcast.extract.edit import Edit
from formcast.extract.model import concrete_paths
from formcast.extract.proof import prove
from formcast.extract.samples import SamplePdf, read_samples
from formcast.extract.setup import FIELDS_LABELS, MAX_SAMPLES, STAGES, SetupSpec, add_line, build_setup

BATCH = r"/api/batches/(?P<batch>[0-9a-f]{32})"
SETUP = r"/api/setups/(?P<setup>[0-9a-f]{32})"
EXTRACTION = r"/api/extractions/(?P<name>[^/]+)"
EDIT = pydantic.TypeAdapter(Edit)  #: an edit as the page sends it, JSON discriminated by "op"
NAME_RULE = "A name is 1 to 80 letters, digits, spaces and _ . -, starting with a letter or digit."


def add_extract_routes(api: Api) -> None:
    """Register the extraction routes on ``api``: the batch, setup and saved extraction routes (the run routes come with their manager)."""

    def route(method: str, pattern: str, handler: Callable[[Api, Request], Any]) -> None:
        api._add(method, pattern, lambda req: handler(api, req))

    route("POST", r"/api/batches", create_batch)
    route("GET", BATCH, get_batch)
    route("POST", BATCH + r"/files", add_batch_files)
    route("GET", BATCH + r"/files", list_batch_files)
    route("POST", BATCH + r"/sample", pick_sample)
    route("GET", BATCH + r"/files/(?P<i>\d{1,7})/pages/(?P<n>\d{1,6})\.png", batch_page_image)
    route("POST", r"/api/setups", reopen_setup)
    route("GET", SETUP, get_setup)
    route("POST", SETUP + r"/edit", edit_setup)
    route("POST", SETUP + r"/learn", learn_wording)
    route("GET", SETUP + r"/samples/(?P<i>\d{1,3})/marks", sample_marks_of)
    route("GET", SETUP + r"/samples/(?P<i>\d{1,3})/pages/(?P<n>\d{1,6})\.png", sample_page_image)
    route("GET", r"/api/extractions", list_extractions)
    route("GET", EXTRACTION, get_extraction)
    route("PUT", EXTRACTION, save_extraction)
    route("DELETE", EXTRACTION, delete_extraction)


def start_extract_job(api: Api, kind: str, payload: dict) -> dict:
    """Start the task job ``kind`` for ``payload`` and return ``{"id": job id}``. ``api.create_job`` calls this for the extract kinds.

    Everything is checked here, before the job's thread starts (see the module docstring for the errors).

    Raises:
        ApiError: 501 ``not_available`` for ``extract-run`` (until the runs' manager exists); the errors above.
    """
    if kind == "extract-run":
        raise ApiError(501, "not_available", f"{kind} is not available yet")
    if api.provider_factory is None:
        raise ApiError(400, "ai_not_configured", AI_NOT_CONFIGURED)
    if kind == "extract-setup":
        return _start_setup(api, payload)
    return _start_line(api, payload)


def _task(api: Api, kind: str, runner: Callable[[Job], None], stages: list[tuple[str, str]] | None = None) -> dict:
    try:
        job = api.jobs.start_task(kind, runner, stages=stages or ())
    except JobsBusy as exc:
        raise ApiError(429, "busy", str(exc)) from None
    return {"id": job.id}


def _stop_if_cancelled(job: Job) -> None:
    if job.cancel_requested:
        raise SessionCancelled("cancelled")


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
    dpi = _dpi(req)
    with missing("file"):
        f = batch.file(int(req.match["i"]))
    try:
        data = f.path.read_bytes()
    except OSError:
        raise ApiError(404, "not_found", f"{f.name} is no longer on the server") from None
    return _png(data, f.name, int(req.match["n"]), dpi)


def _dpi(req: Request) -> int:
    """The query's ``dpi`` (``DEFAULT_DPI`` when it has none; ``page_png`` clamps it); 400 ``bad_dpi`` when it is no number."""
    try:
        return int(req.query.get("dpi", str(DEFAULT_DPI)))
    except ValueError:
        raise ApiError(400, "bad_dpi", f"dpi must be a whole number from {MIN_DPI} to {MAX_DPI}") from None


def _png(data: bytes, name: str, n: int, dpi: int) -> Response:
    """Page ``n`` of the PDF ``data`` (called ``name``) as a PNG response: 404 for no such page, 422 for a PDF MuPDF can't open."""
    try:
        png = page_png(data, n, dpi)
    except IndexError:
        raise ApiError(404, "not_found", f"{name} has no page {n + 1}") from None
    except Exception:  # MuPDF raises several types for a file it cannot open
        raise ApiError(422, "bad_pdf", f"{name} can't be shown: it is not a PDF that can be read.") from None
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


# ------------------------------------------------------------------ setup jobs


def _start_setup(api: Api, payload: dict) -> dict:
    """``{batch, samples, spec}`` -> an ``extract-setup`` job: ``build_setup`` on the batch's files ``samples``."""
    try:
        batch = api.batches.get(payload.get("batch"))
    except (KeyError, TypeError):
        raise ApiError(400, "bad_batch", "Send batch as the id of a batch this server has open.") from None
    indexes = _sample_indexes(batch, payload.get("samples"))
    spec = _spec(api, payload.get("spec"))
    samples = []
    for i in indexes:
        f = batch.file(i)
        try:
            samples.append(SamplePdf(f.name, f.path.read_bytes()))
        except OSError:
            raise ApiError(404, "not_found", f"{f.name} is no longer on the server") from None
    warmup = api.documents.detectors
    stages = [(key, FIELDS_LABELS[spec.start] if key == "fields" else label) for key, label in STAGES]

    def run(job: Job) -> None:
        def on_stage(event) -> None:
            job.stage(event.key, event.status, event.detail)
            _stop_if_cancelled(job)
            if event.key == "detect" and event.status == "running" and warmup is not None:
                warmup.ready()  # the weights load before the library takes the lock to detect

        provider = api.provider_factory()
        try:
            result = build_setup(
                samples, spec, provider, detectors=warmup.detectors if warmup is not None else None,
                on_stage=on_stage, on_page=lambda i, progress: _stop_if_cancelled(job),
            )  # fmt: skip
        finally:
            provider.close()
        job.add_usage(result.usage)
        setup = Setup(uuid.uuid4().hex, batch.id, result.template, result.samples, indexes, result.proof, result.warnings)
        job.set_result(setup=api.setups.add(setup).summary())

    return _task(api, "extract-setup", run, stages)


def _sample_indexes(batch: Batch, raw: Any) -> list[int]:
    """``raw`` as the files of ``batch`` to build from: 3 to 20 of them (all of a smaller batch), each once; 400 ``bad_sample`` otherwise."""
    count = batch.summary()["count"]
    fewest = max(1, min(MIN_SAMPLE, count))
    if not isinstance(raw, list) or not all(type(i) is int for i in raw):
        raise ApiError(400, "bad_sample", "Send samples as the numbers of the batch's files to build the template from.")
    if not fewest <= len(raw) <= MAX_SAMPLES:
        raise ApiError(400, "bad_sample", f"Build the template from {fewest} to {MAX_SAMPLES} of the batch's files.")
    for i in raw:
        if not 0 <= i < count:
            raise ApiError(400, "bad_sample", f"{i} is not a file of this batch (it has {count}).")
    if len(set(raw)) != len(raw):
        raise ApiError(400, "bad_sample", "Pick each file once.")
    return list(raw)


def _spec(api: Api, raw: Any) -> SetupSpec:
    """The request's ``spec`` as a ``SetupSpec``, its ``destination`` (a fill template's name) made that template's schema.

    Raises:
        ApiError: 400 ``bad_spec`` for a spec that is not valid; 404 ``not_found`` for an unknown destination.
    """
    if not isinstance(raw, dict):
        raise ApiError(400, "bad_spec", "Send spec as a JSON object.")
    raw = dict(raw)
    destination = raw.get("destination")
    if destination is not None:
        if not isinstance(destination, str):
            raise ApiError(400, "bad_spec", "Send destination as the name of a fill template.")
        try:
            with missing("template"):
                raw["destination"] = api.store.get(destination).schema_
        except ValueError:  # a name the store refuses: no template has it
            raise ApiError(404, "not_found", f"no such template: {destination}") from None
        raw["destination_name"] = destination
    try:
        spec = SetupSpec.model_validate(raw)
    except pydantic.ValidationError as exc:
        raise ApiError(400, "bad_spec", _first_error(exc)) from None
    if spec.destination is not None:
        try:
            concrete_paths(spec.destination)
        except (FormcastError, ValueError) as exc:
            raise ApiError(400, "bad_spec", f"The destination's fields can't be used: {exc}") from None
    return spec


def _start_line(api: Api, payload: dict) -> dict:
    """``{setup, line}`` -> an ``extract-line`` job: ``add_line`` on the setup's template and samples."""
    setup = _setup_of(api, payload.get("setup"))
    line = payload.get("line")
    if not isinstance(line, str) or not line.strip():
        raise ApiError(400, "bad_request", "Send line as the words for the fields to add.")
    line = line.strip()
    try:
        template, samples = setup.start_line()
    except SetupBusy as exc:
        raise ApiError(409, "busy_setup", str(exc)) from None

    def run(job: Job) -> None:
        kept = None
        try:
            provider = api.provider_factory()
            try:
                vision = template.setup.vision if template.setup is not None else True
                result = add_line(template, line, samples, provider, vision=vision)
            finally:
                provider.close()
            job.add_usage(result.usage)
            _stop_if_cancelled(job)
            kept = result
        finally:
            setup.end_line(kept)
        old = set(concrete_paths(template.schema_))
        job.set_result(setup=setup.summary(), paths=[p for p in concrete_paths(kept.template.schema_) if p not in old])

    try:
        return _task(api, "extract-line", run)
    except BaseException:  # no job started: the setup takes changes again
        setup.end_line(None)
        raise


# ------------------------------------------------------------------ setups


def _setup(api: Api, req: Request) -> Setup:
    with missing("setup"):
        return api.setups.get(req.match["setup"])


def _setup_of(api: Api, setup_id: Any) -> Setup:
    """The setup a request body names: 400 ``bad_request`` for no id, 404 ``not_found`` for an unknown one."""
    if not isinstance(setup_id, str):
        raise ApiError(400, "bad_request", "Send setup as the id of a setup.")
    with missing("setup"):
        return api.setups.get(setup_id)


def get_setup(api: Api, req: Request) -> dict:
    return _setup(api, req).summary()


def reopen_setup(api: Api, req: Request) -> dict:
    """``{extraction}`` -> a new setup of that saved template: its samples read again (no detection) and proven."""
    name = _object(req).get("extraction")
    if not isinstance(name, str):
        raise ApiError(400, "bad_request", "Send extraction as the name of a saved extraction template.")
    _checked(name)
    with missing("extraction"):
        template = api.extractions.get(name)
        pdfs = api.extractions.samples(name)
    ocr = template.setup.ocr if template.setup is not None else "auto"
    docs = read_samples(pdfs, detect="off", ocr=ocr)
    proof = prove(template, [doc.pdf for doc in docs])
    return api.setups.add(Setup(uuid.uuid4().hex, None, template, docs, None, proof, [])).summary()


def edit_setup(api: Api, req: Request) -> dict:
    """``{edit}`` (an ``Edit`` as JSON) made on the setup's template, which is proven again; no model call."""
    setup = _setup(api, req)
    try:
        edit = EDIT.validate_python(_object(req).get("edit"))
    except pydantic.ValidationError as exc:
        raise ApiError(400, "bad_edit", f"this is not an edit: {_first_error(exc)}") from None
    try:
        setup.edit(edit)
    except SetupBusy as exc:
        raise ApiError(409, "busy_setup", str(exc)) from None
    except FormcastError as exc:
        raise ApiError(400, "bad_edit", str(exc)) from None
    except pydantic.ValidationError as exc:
        raise ApiError(400, "bad_edit", f"the edited template is not valid: {_first_error(exc)}") from None
    return setup.summary()


def learn_wording(api: Api, req: Request) -> dict:
    """``{path}``: the wordings the proof suggests for that path learned into its reader's map; proven again, no model call."""
    setup = _setup(api, req)
    path = _object(req).get("path")
    if not isinstance(path, str):
        raise ApiError(400, "bad_request", "Send path as the path of a field.")
    try:
        setup.learn(path)
    except SetupBusy as exc:
        raise ApiError(409, "busy_setup", str(exc)) from None
    except FormcastError as exc:
        raise ApiError(400, "bad_request", str(exc)) from None
    return setup.summary()


def sample_marks_of(api: Api, req: Request) -> dict:
    """The marks of the setup's sample ``i``: where each path is read on it, its boxes, labels and options."""
    setup = _setup(api, req)
    with missing("sample"):
        marks = setup.marks(int(req.match["i"]))
    return {"marks": [mark.model_dump(mode="json") for mark in marks]}


def sample_page_image(api: Api, req: Request) -> Response:
    """``GET .../samples/{i}/pages/{p}.png?dpi=110`` (both count from 0): a page of one of the setup's samples."""
    setup = _setup(api, req)
    dpi = _dpi(req)
    with missing("sample"):
        doc = setup.sample(int(req.match["i"]))
    return _png(doc.pdf, doc.file, int(req.match["n"]), dpi)


# ------------------------------------------------------------------ saved extraction templates


def _checked(name: str) -> str:
    try:
        return check_name(name)
    except ValueError:
        raise ApiError(400, "bad_name", NAME_RULE) from None


def list_extractions(api: Api, req: Request) -> dict:
    return {"extractions": api.extractions.list()}


def get_extraction(api: Api, req: Request) -> dict:
    name = _checked(req.match["name"])
    with missing("extraction"):
        return {"template": api.extractions.get(name).model_dump(mode="json", by_alias=True)}


def save_extraction(api: Api, req: Request) -> dict:
    """``{setup}`` -> the setup's template saved as this name (its ``name`` field too), with the setup's sample PDFs."""
    name = _checked(req.match["name"])
    setup = _setup_of(api, _object(req).get("setup"))
    template, samples = setup.snapshot()
    saved = template.model_copy(update={"name": name})
    api.extractions.put(name, saved, [SamplePdf(doc.file, doc.pdf) for doc in samples])
    return {"name": name, "template_id": saved.template_id}


def delete_extraction(api: Api, req: Request) -> dict:
    name = _checked(req.match["name"])
    with missing("extraction"):
        api.extractions.delete(name)
    return {"name": name}


def _first_error(exc: pydantic.ValidationError) -> str:
    """The first problem pydantic found, as ``where: what`` (``what`` alone when it is about the whole object)."""
    first = exc.errors()[0]
    where = ".".join(str(part) for part in first["loc"])
    message = str(first["msg"]).removeprefix("Value error, ")
    return f"{where}: {message}" if where else message
