"""``python -m demo.server [--host H] [--port P] [--data-dir D]``, run from the repository root.

Run the workbench on your own machine only. It binds 127.0.0.1 unless you pass ``--host``, and
the agent's sandbox is not a security boundary.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import formcast  # noqa: F401
except ImportError:
    # Not installed (`pip install -e .` is the normal way): use this checkout's src/ so that
    # `python -m demo.server` also works straight from a clone.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import argparse
import logging
from collections.abc import Mapping, Sequence
from http.server import ThreadingHTTPServer

from demo.server.detectors import DetectorWarmup
from demo.server.server import make_server
from demo.server.settings import PORT_RANGE, Settings, parse_port


WILDCARD_HOSTS = ("0.0.0.0", "::", "")


def _port(text: str) -> int:
    """argparse type for ``--port``: a bad value is a one-line usage error, not a traceback at bind time."""
    try:
        return parse_port(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be {PORT_RANGE}") from None


def build(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> ThreadingHTTPServer:
    """The server for these command-line flags. Settings come from ``env`` (default: ``.env`` plus the environment).

    The server gets a detector warm-up that is not started yet; ``main`` starts it.
    """
    parser = argparse.ArgumentParser(prog="python -m demo.server", description="Run the formcast workbench on this machine.")
    parser.add_argument("--host", help="address to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=_port, help="port (default 8765; 0 picks a free one)")
    parser.add_argument("--data-dir", type=Path, help="folder with forms/, schemas/, records/, templates/ (default demo/data)")
    parser.add_argument(
        "--no-demo", action="store_true",
        help="leave out the demo forms, shipped templates and demo records: only your own saved templates and PDFs",
    )  # fmt: skip
    args = parser.parse_args(argv)
    settings = Settings.from_env(env)
    if args.host:
        settings.host = args.host
    if args.port is not None:
        settings.port = args.port
    if args.data_dir is not None:
        settings.data_dir = args.data_dir.expanduser().resolve()
    if args.no_demo:
        settings.demo_enabled = False
    return make_server(settings, detectors=DetectorWarmup())


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        httpd = build(argv)
    except (ValueError, OSError) as exc:  # a bad setting or a port in use: say so plainly
        print(f"Cannot start the workbench: {exc}", file=sys.stderr)
        return 2
    host, port = httpd.server_address[:2]
    if host in WILDCARD_HOSTS:
        # The server refuses any Host header that is not loopback or its bind address, so the wildcard
        # address itself is not a URL anyone can open.
        print(f"Workbench: http://127.0.0.1:{port}/workbench/", flush=True)
        print(
            f"Listening on every address ({host}). Only this machine's own URL works; to use the workbench "
            "from another machine on your network, bind that machine's IP with --host.",
            file=sys.stderr,
        )
    else:
        print(f"Workbench: http://{host}:{port}/workbench/", flush=True)
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"Warning: listening on {host}. Anyone who can reach this machine can use the workbench.", file=sys.stderr)
    if httpd.detectors is not None:
        httpd.detectors.start()  # loads (on a first run, downloads) the detector weights while you pick a PDF
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
