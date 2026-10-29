"""The extraction workbench's managers: batches of PDFs, setups, saved extractions and runs.

This module is where they live; ``Api`` holds one of each (``api.batches``, ``api.setups``, ``api.extractions``,
``api.runs``), and ``demo/server/extract_api.py`` is the HTTP face over them. Everything they write goes under
``<data_dir>/local/`` (``batches/``, ``extractions/``, ``runs/``). The managers are added one at a time; until
a manager exists its ``Api`` attribute is ``None``.
"""

from __future__ import annotations
