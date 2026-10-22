"""Where the workbench keeps templates.

Replace with your database: implement ``TemplateStore`` (four methods) and hand your object to
the server in place of ``FolderTemplateStore``. ``get`` and ``delete`` raise ``KeyError`` for a
name that isn't stored; every method raises ``ValueError("bad template name")`` for a name that
isn't plain (letters, digits, space, ``_ . -``, at most 80 characters, starting with a letter or
digit), so a name can never reach outside the folder.

``get`` raises ``StoredTemplateError`` (not ``ValueError``) when the name is fine and a file exists
but isn't a valid template, so a caller can tell a broken stored file from a bad request.

``FolderTemplateStore`` keeps one ``<name>.json`` per template and writes through a temporary
file and ``os.replace``, so a crash mid-write leaves the old file whole.

``OverlayTemplateStore`` is what the workbench uses by default: your saves go to a git-ignored folder
(``<data>/local/templates``) laid over the templates that ship with the demo (``<data>/templates``),
which it never writes. A saved template hides a shipped one with the same name; deleting it brings
the shipped one back; deleting a shipped one is refused with ``ShippedTemplateError``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, TypedDict

from formcast import Template

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}")


class StoredTemplate(TypedDict):
    name: str
    template_id: str
    page_count: int
    updated: str  # ISO 8601, UTC


class TemplateStore(Protocol):
    def list(self) -> list[StoredTemplate]: ...
    def get(self, name: str) -> Template: ...
    def put(self, name: str, template: Template) -> None: ...
    def delete(self, name: str) -> None: ...


class StoredTemplateError(Exception):
    """A stored file exists but is not a valid template (damaged, hand-edited, or from elsewhere)."""


class ShippedTemplateError(Exception):
    """A template that ships with the demo data, which the store does not delete."""


def check_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError("bad template name")
    return name


class FolderTemplateStore:
    def __init__(self, folder: Path | str) -> None:
        self.folder = Path(folder)

    def _path(self, name: str) -> Path:
        return self.folder / f"{check_name(name)}.json"

    def exists(self, name: str) -> bool:
        """Whether a file is stored under ``name`` (valid template or not)."""
        return self._path(name).is_file()

    def list(self) -> list[StoredTemplate]:
        out: list[StoredTemplate] = []
        for path in sorted(self.folder.glob("*.json")):
            if not NAME_RE.fullmatch(path.stem):
                continue
            try:
                form = json.loads(path.read_text(encoding="utf-8"))
                updated = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                out.append({
                    "name": path.stem,
                    "template_id": str(form["template_id"]),
                    "page_count": int(form["form"]["page_count"]),
                    "updated": updated.isoformat(timespec="seconds"),
                })  # fmt: skip
            except (OSError, ValueError, KeyError, TypeError):
                continue  # a file that isn't a template is not listed
        return out

    def get(self, name: str) -> Template:
        path = self._path(name)  # a bad name raises ValueError here, before any file is read
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise KeyError(name) from None
        except UnicodeDecodeError:
            raise StoredTemplateError(f"stored template {name!r} is not valid UTF-8 text") from None
        try:
            return Template.from_json(text)
        except (ValueError, TypeError, KeyError) as exc:  # bad JSON, or JSON that isn't a template
            raise StoredTemplateError(f"stored template {name!r} is not a valid template") from exc

    def put(self, name: str, template: Template) -> None:
        path = self._path(name)
        self.folder.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.folder, prefix=".tmp-", suffix=".part")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(template.to_json())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def delete(self, name: str) -> None:
        try:
            self._path(name).unlink()
        except FileNotFoundError:
            raise KeyError(name) from None


class OverlayTemplateStore:
    """Saved templates in ``local`` over read-only shipped ones in ``shipped`` (see the module docstring)."""

    def __init__(self, local: Path | str, shipped: Path | str) -> None:
        self.local = FolderTemplateStore(local)
        self.shipped = FolderTemplateStore(shipped)

    def list(self) -> list[StoredTemplate]:
        mine = self.local.list()
        names = {t["name"] for t in mine}
        return sorted([*mine, *(t for t in self.shipped.list() if t["name"] not in names)], key=lambda t: t["name"])

    def get(self, name: str) -> Template:
        try:
            return self.local.get(name)  # a bad name raises ValueError here
        except KeyError:
            return self.shipped.get(name)

    def put(self, name: str, template: Template) -> None:
        self.local.put(name, template)

    def delete(self, name: str) -> None:
        if self.local.exists(name):
            self.local.delete(name)
        elif self.shipped.exists(name):
            raise ShippedTemplateError(
                f"{name} came with the demo, so it can't be deleted. Saving under the same name keeps your own "
                "version in its place."
            )
        else:
            raise KeyError(name)
