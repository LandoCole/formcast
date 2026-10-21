"""Wire format between :class:`~formcast.sandbox.client.Sandbox` and the worker.

One JSON object per line. The parent writes requests to the worker's stdin; the
worker answers each request with exactly one response line on its stdout. The
worker moves its own fd 1 away from that pipe at startup, so nothing but
protocol messages ever reaches it (user ``print`` output is captured per exec).

Requests (``cmd``):

* ``init``: ``form_map`` (``FormMap`` JSON), ``schema`` (list of ``SchemaPath``
  dicts), ``template`` (``Template`` JSON as a dict), ``max_stdout`` (int).
  Builds a fresh namespace.
* ``update_template``: ``template``. Replaces the snapshot behind ``tpl``.
* ``exec``: ``code``. Runs it in the persistent namespace.

Every request carries an integer ``seq`` (increasing per worker); the response
echoes it, so the parent can drop a stale response to a request it gave up on.

Responses: ``{"ok": true, "seq": int, ...}`` or ``{"ok": false, "seq": int,
"error": str}``. An ``exec`` response carries ``stdout``, ``truncated``, ``ops``
and ``error`` (None when the code ran to the end).
"""

from __future__ import annotations

import json
from typing import Any

INIT = "init"
UPDATE_TEMPLATE = "update_template"
EXEC = "exec"


def encode(message: dict[str, Any]) -> bytes:
    """One message as a newline-terminated UTF-8 JSON line."""
    return (json.dumps(message, ensure_ascii=True) + "\n").encode("utf-8")


def decode(line: bytes | str) -> dict[str, Any]:
    """Parse one line into a message dict; raises ``ValueError`` on anything else."""
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ValueError(f"protocol message must be a JSON object, got {type(message).__name__}")
    return message
