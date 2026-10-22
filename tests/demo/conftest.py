"""Fixtures for the browser demo tests: a static file server and Playwright.

``server`` serves the repository root over HTTP on an ephemeral port, so ``demo/web/...``
is reachable as ``{server}/demo/web/...`` exactly as the demo is served in use. ``browser`` and
``page`` skip the test with a clear reason when Playwright or Chromium is not installed.
"""

from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


class _QuietHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".mjs": "text/javascript",
        ".js": "text/javascript",
        ".wasm": "application/wasm",
    }

    def log_message(self, format, *args):  # noqa: A002 - signature fixed by the base class
        pass


@pytest.fixture(scope="session")
def server():
    """Base URL (no trailing slash) of a static server rooted at the repository root."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(ROOT)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def browser():
    """A headless Chromium shared by the session; skips when Playwright or Chromium is missing."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright is not installed (pip install -e '.[dev]')")
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except PlaywrightError as exc:
            pytest.skip(f"Chromium is not available (run: playwright install chromium): {str(exc).splitlines()[0]}")
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def page(browser):
    """A fresh page in its own browser context."""
    context = browser.new_context()
    try:
        yield context.new_page()
    finally:
        context.close()
