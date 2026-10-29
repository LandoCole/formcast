"""A stand-in for the batch worker that kills its process on one document, as MuPDF crashing on a file would.

Tests put :func:`work` in place of ``formcast.extract.batch._work``; ``submit`` pickles it by reference, so spawned
workers import this module and run it.
"""

import os

from formcast.extract.batch import _work

#: The document whose reading kills the worker process.
CRASH_ON = "d.pdf"


def work(file: str, doc_id: str) -> str:
    if doc_id == CRASH_ON:
        os._exit(1)
    return _work(file, doc_id)
