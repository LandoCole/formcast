"""Stand-ins for the batch worker that kill its process, or hang, on one document, as MuPDF failing on a file would.

Tests put :func:`work` or :func:`hang` in place of ``formcast.extract.batch._work``; ``submit`` pickles them by
reference, so spawned workers import this module and run them.
"""

import os
import time

from formcast.extract.batch import _work

#: The document whose reading kills the worker process.
CRASH_ON = "d.pdf"
#: The document whose reading never ends (until the worker is terminated).
HANG_ON = "h.pdf"


def work(file: str, doc_id: str) -> str:
    if doc_id == CRASH_ON:
        os._exit(1)
    return _work(file, doc_id)


def hang(file: str, doc_id: str) -> str:
    if doc_id == HANG_ON:
        time.sleep(3600)
    return _work(file, doc_id)
