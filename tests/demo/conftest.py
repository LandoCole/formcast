"""Fixtures for the browser demo tests: a static file server and Playwright.

``server`` serves the repository root over HTTP on an ephemeral port, so ``demo/web/...``
is reachable as ``{server}/demo/web/...`` exactly as the demo is served in use. ``browser`` and
``page`` skip the test with a clear reason when Playwright or Chromium is not installed.

``demo_server`` is the workbench server (``demo.server``) on an ephemeral port, over a throwaway copy of
``demo/data`` so nothing a test saves reaches the repository. The copy leaves out ``demo/data/local/`` (the
templates a developer saved in the workbench), so every test sees the shipped data only. ``start_demo_server(**kw)`` starts another
one with different settings, a provider factory, a store or records. ``text_only_agent`` makes AI jobs run the
agent with ``vision=False``.
"""

from __future__ import annotations

import shutil
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEMO_DATA = ROOT / "demo" / "data"


def copy_demo_data(src: Path, dst: Path) -> None:
    """Copy the demo data folder ``src`` to ``dst``, without its ``local/`` folder (a developer's own saves)."""
    shutil.copytree(src, dst, ignore=lambda folder, names: ["local"] if Path(folder) == Path(src) and "local" in names else [])


@pytest.fixture
def text_only_agent(monkeypatch):
    """AI jobs run the agent with ``vision=False``: for tests that read its first message as text."""
    from demo.server import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "build_template", partial(jobs_mod.build_template, vision=False))
    monkeypatch.setattr(jobs_mod, "revise", partial(jobs_mod.revise, vision=False))


@pytest.fixture
def demo_data_copier():
    """``copy_demo_data`` (above), for the test that checks it."""
    return copy_demo_data


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


@pytest.fixture
def start_demo_server(tmp_path):
    """Factory: ``start_demo_server(provider_factory=None, store=None, records=None, **settings)`` -> base URL.

    Keyword arguments other than those three set attributes of the ``Settings`` (built from an empty mapping, so the
    real ``.env`` is never read), e.g. ``max_upload_bytes=1024`` or ``llm=LLMConfig(...)``. Every server is shut
    down at teardown. ``start_demo_server.servers`` lists them, so a test can reach ``servers[-1].api``.
    """
    from demo.server.server import make_server
    from demo.server.settings import Settings

    servers: list[ThreadingHTTPServer] = []
    threads: list[threading.Thread] = []
    data_dir = tmp_path / "data"
    if not data_dir.exists():
        copy_demo_data(DEMO_DATA, data_dir)

    def start(*, provider_factory=None, store=None, records=None, **settings_overrides) -> str:
        settings = Settings.from_env({})
        settings.host, settings.port, settings.data_dir = "127.0.0.1", 0, data_dir
        for key, value in settings_overrides.items():
            if not hasattr(settings, key):
                raise AttributeError(key)
            setattr(settings, key, value)
        httpd = make_server(settings, provider_factory=provider_factory, store=store, records=records)
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        servers.append(httpd)
        threads.append(thread)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    start.servers = servers  # type: ignore[attr-defined]
    try:
        yield start
    finally:
        for httpd in servers:
            httpd.shutdown()
            httpd.server_close()
        for thread in threads:
            thread.join(timeout=5)


@pytest.fixture
def demo_server(start_demo_server):
    """Base URL (no trailing slash) of a workbench server with default settings and no AI provider."""
    return start_demo_server()


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
