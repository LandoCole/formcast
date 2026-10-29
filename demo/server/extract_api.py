"""The extraction workbench's HTTP routes and jobs, kept apart from ``demo/server/api.py``.

    add_extract_routes(api)           registers the routes on an ``Api`` (called at the end of ``Api._add_routes``)
    start_extract_job(api, kind, payload) -> {"id": ...}    starts an ``extract-setup``, ``extract-line`` or
                                      ``extract-run`` task job (``Jobs.start_task``); ``Api.create_job`` hands these
                                      kinds over after reading the request

The handlers are functions over the ``Api`` they are registered on, so the managers (``demo/server/extraction.py``)
stay where the ``Api`` keeps them. This module imports ``api`` for ``ApiError``, ``Request`` and the upload helpers,
so ``api`` imports it only inside the methods that call it.
"""

from __future__ import annotations

from demo.server.api import Api, ApiError


def add_extract_routes(api: Api) -> None:
    """Register the extraction routes on ``api``. None yet: the batch, setup, extraction and run routes come with their managers."""


def start_extract_job(api: Api, kind: str, payload: dict) -> dict:
    """Start the task job ``kind`` for ``payload`` and return ``{"id": job id}``. ``api.create_job`` calls this for the extract kinds.

    Raises:
        ApiError: 501 ``not_available`` until the kind's manager exists.
    """
    raise ApiError(501, "not_available", f"{kind} is not available yet")
