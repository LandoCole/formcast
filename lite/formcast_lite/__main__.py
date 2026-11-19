"""Start FormCast Lite: serve on 127.0.0.1 and open the page in the default browser.

    python -m formcast_lite [--port 8790] [--no-browser]
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from formcast_lite import __version__
from formcast_lite.detector import Detector
from formcast_lite.jobs import Queue
from formcast_lite.server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="formcast-lite", description="Convert static PDFs into fillable PDFs.")
    parser.add_argument("--port", type=int, default=8790, help="port to listen on (another is picked if taken)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = parser.parse_args(argv)

    httpd = serve(Queue(Detector()), port=args.port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"formcast lite {__version__} is running at {url}", flush=True)
    print("Nothing is saved: closing this window quits the app and forgets every document.", flush=True)
    if not args.no_browser:
        threading.Timer(0.3, webbrowser.open, (url,)).start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
