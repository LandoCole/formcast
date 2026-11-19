"""Entry point for PyInstaller (and ``python run.py``)."""

import multiprocessing
import sys

from formcast_lite.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
