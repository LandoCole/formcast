"""Data the person brought to fill forms with: an uploaded table or pasted text, held in memory by id.

    Datasets.add_file(bytes, name) -> Dataset   a CSV, TSV, Excel workbook or JSON file
    Datasets.add_text(text) -> Dataset          pasted text: JSON records or a delimited table become a
                                                table, anything else stays free text for the model to read
    Datasets.get(id) -> Dataset                 KeyError if unknown or dropped

A ``Dataset`` is either a ``table`` (``formcast.data.Table``) or ``text``. Its ``summary()`` is what the
API returns: name, kind, row count, columns with their inferred types and samples (the data's own
schema) and the first rows. Like documents, nothing is written to disk and the 20 most recently used
are kept.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Literal

from formcast.data import Table, data_schema, profile, read_table, sniff_text

MAX_DATASETS = 20
MAX_TEXT_CHARS = 60_000
PREVIEW_ROWS = 5


@dataclass
class Dataset:
    id: str
    name: str
    kind: Literal["table", "text"]
    table: Table | None = None
    text: str = ""

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "name": self.name, "kind": self.kind}
        if self.table is not None:
            t = self.table
            out.update({
                "format": t.kind,
                "sheet": t.sheet,
                "rows": len(t.rows),
                "truncated": t.truncated,
                "columns": [vars(p) for p in profile(t)],
                "schema": data_schema(t),
                "preview": t.rows[:PREVIEW_ROWS],
            })  # fmt: skip
        else:
            out.update({"chars": len(self.text), "preview": self.text[:400]})
        return out


class Datasets:
    def __init__(self, limit: int = MAX_DATASETS) -> None:
        self.limit = limit
        self._items: OrderedDict[str, Dataset] = OrderedDict()
        self._lock = threading.Lock()

    def _keep(self, ds: Dataset) -> Dataset:
        with self._lock:
            self._items[ds.id] = ds
            while len(self._items) > self.limit:
                self._items.popitem(last=False)
        return ds

    def add_file(self, data: bytes, name: str) -> Dataset:
        """Read a file as a table. Raises ``FormcastError`` with a plain message when it can't be read."""
        return self._keep(Dataset(uuid.uuid4().hex, name, "table", table=read_table(data, name)))

    def add_text(self, text: str, name: str = "pasted text") -> Dataset:
        """Pasted text: a table when it is one (see ``formcast.data.sniff_text``), else free text."""
        table = sniff_text(text)
        if table is not None:
            return self._keep(Dataset(uuid.uuid4().hex, table.name, "table", table=table))
        return self._keep(Dataset(uuid.uuid4().hex, name, "text", text=text[:MAX_TEXT_CHARS]))

    def add_table(self, table: Table) -> Dataset:
        return self._keep(Dataset(uuid.uuid4().hex, table.name or "records", "table", table=table))

    def get(self, ds_id: str) -> Dataset:
        with self._lock:
            ds = self._items[ds_id]
            self._items.move_to_end(ds_id)
            return ds
