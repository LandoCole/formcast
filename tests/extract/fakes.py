"""Fake models for the extraction setup tests: a scripted one and one that answers from the corpus's own truth.

``Scripted(*answers)`` answers each call with the next text, as ``tests/data/test_ai.py``'s does. ``CorpusModel``
answers every call setup makes by its system prompt, the way a model that reads the corpus form perfectly would:

    DERIVE_SYSTEM    the hand template's schema
    FIELDS_SYSTEM    the hand template's fields whose names or titles share a word with each line
    ON_FORM_SYSTEM   the asked destination fields that are fields of the hand template
    READERS_SYSTEM   the hand template's readers for the asked paths, as proposals (widgets, labels, phrases,
                     options by form field, clean steps); its boxes are left to resolve, which puts one at each
                     widget's place
    VALUES_SYSTEM    ``truth(client)`` of the sample the message names, for the asked paths (``None`` elsewhere)

It is thread-safe, counts its calls by kind (``model.calls == {}``: no call was made) and is its own provider
factory (``model()`` returns it), for the workbench server's tests.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any

from formcast.data.ai import DERIVE_SYSTEM
from formcast.extract.ask import _DESTINATION_HEADING, _LINES_HEADING, _PATHS_HEADING, _leaves
from formcast.extract.model import AnchorRead, ChoiceRead, PhraseRead, Reader, WidgetRead
from formcast.extract.prompts import FIELDS_SYSTEM, ON_FORM_SYSTEM, READERS_SYSTEM, VALUES_SYSTEM
from formcast.llm import LLMResponse, Message, TextPart, Usage
from tests.extract.corpus import Client, hand_template, truth


class Scripted:
    """Answers each call with the next text; records what it was sent."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.calls: list[list] = []

    def complete(self, messages, tools, **kwargs):
        self.calls.append(list(messages))
        return LLMResponse(text=self.answers.pop(0), tool_calls=[], usage=Usage(10, 5), raw={})

    def close(self) -> None:
        pass


def user_text(message: Message) -> str:
    """The text of a message, its text parts joined by newlines."""
    if isinstance(message.content, str):
        return message.content
    return "\n".join(p.text for p in message.content if isinstance(p, TextPart))


def _block(text: str, heading: str) -> list[str]:
    """The lines after the line that starts with ``heading``, up to the next blank line."""
    lines = text.splitlines()
    start = next((n for n, line in enumerate(lines) if line.startswith(heading)), None)
    if start is None:
        return []
    out = []
    for line in lines[start + 1 :]:
        if not line.strip():
            break
        out.append(line)
    return out


def asked_paths(text: str) -> list[str]:
    """The paths a readers or values message asks about, in order."""
    return [line.strip() for line in _block(text, _PATHS_HEADING)]


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _restricted(node: dict, prefix: str, wanted: set[str]) -> dict | None:
    """``node`` with only the leaf paths in ``wanted`` (``None`` when it keeps none); ``required`` follows."""
    if node.get("type") == "object":
        props = {}
        for name, sub in node.get("properties", {}).items():
            kept = _restricted(sub, f"{prefix}.{name}" if prefix else name, wanted)
            if kept is not None:
                props[name] = kept
        if not props:
            return None
        out = {**node, "properties": props}
        out.pop("required", None)
        required = [name for name in node.get("required", []) if name in props]
        return {**out, "required": required} if required else out
    if node.get("type") == "array" and node.get("items", {}).get("type") == "object":
        items = _restricted(node["items"], f"{prefix}[]", wanted)
        return None if items is None else {**node, "items": items}
    return dict(node) if prefix in wanted else None


def _proposal(path: str, reader: Reader) -> dict[str, Any]:
    """A reader as a model would propose it: what resolve can check on the samples."""
    out: dict[str, Any] = {"path": path}
    for s in reader.strategies:
        if isinstance(s, WidgetRead):
            out.setdefault("widgets", []).extend(s.names)
        elif isinstance(s, AnchorRead):
            out.update(labels=list(s.labels), side=s.side, max_width=s.max_width, max_lines=s.max_lines)
        elif isinstance(s, PhraseRead):
            out.update(phrases=list(s.phrases), stop=s.stop)
        elif isinstance(s, ChoiceRead):
            for key, mark in s.options.items():
                if mark.widget is not None:  # a drawn box is a rectangle, which a proposal cannot name
                    option = {"widget": mark.widget, **({"state": mark.state} if mark.state else {})}
                    out.setdefault("options", {})[key] = option
    if reader.clean:
        out["clean"] = [t.model_dump() for t in reader.clean]
    return out


class CorpusModel:
    """Answers setup's calls from the corpus, as the module docstring says; ``clients_by_file`` maps files to clients."""

    def __init__(self, clients_by_file: dict[str, Client]) -> None:
        self.clients = dict(clients_by_file)
        self.calls: dict[str, int] = {}
        self._lock = threading.Lock()
        template = hand_template()
        self.schema = template.schema_
        self.readers = template.readers

    def __call__(self) -> CorpusModel:
        return self

    def complete(self, messages, tools, **kwargs):
        system, text = messages[0].content, user_text(messages[1])
        if system.startswith(DERIVE_SYSTEM.split("Reply with one JSON object")[0]):  # DERIVE_SYSTEM, or with the required rules before its reply sentence
            kind, answer = "derive", {"schema": self.schema}
        elif system == FIELDS_SYSTEM:
            kind, answer = "fields", self._fields(text)
        elif system == ON_FORM_SYSTEM:
            kind, answer = "on_form", self._on_form(text)
        elif system == READERS_SYSTEM:
            kind, answer = "readers", {"readers": [self._reader(p) for p in asked_paths(text)]}
        elif system == VALUES_SYSTEM:
            kind, answer = "values", self._values(text)
        else:
            raise AssertionError(f"CorpusModel was sent a system prompt it does not know: {str(system)[:80]!r}")
        with self._lock:
            self.calls[kind] = self.calls.get(kind, 0) + 1
        return LLMResponse(text=json.dumps(answer), tool_calls=[], usage=Usage(10, 5), raw={})

    def close(self) -> None:
        pass

    def _fields(self, text: str) -> dict:
        named = {p: f"{p} {_node(self.schema, p).get('title', '')}".replace("_", " ") for p in _leaves(self.schema)}
        names = {p: _words(text) for p, text in named.items()}
        line_paths: dict[str, list[str]] = {}
        for line in _block(text, _LINES_HEADING):
            number, _, wording = line.partition(". ")
            if number.isdigit():
                line_paths[wording] = [p for p, words in names.items() if _words(wording) & words]
        wanted = {p for paths in line_paths.values() for p in paths}
        schema = _restricted(self.schema, "", wanted) or {"type": "object", "properties": {}}
        return {"schema": schema, "line_paths": line_paths}

    def _on_form(self, text: str) -> dict:
        asked = [line[2:].split(": ", 1)[0] for line in _block(text, _DESTINATION_HEADING) if line.startswith("- ")]
        known = set(_leaves(self.schema))
        return {"on_form": [p for p in asked if p in known]}

    def _reader(self, path: str) -> dict[str, Any]:
        reader = self.readers.get(path)
        return {"path": path} if reader is None else _proposal(path, reader)

    def _values(self, text: str) -> dict:
        found = re.search(r"^SAMPLE \d+ \((.*), (?:fillable|flattened), \d+ pages?\)", text, re.MULTILINE)
        client = self.clients.get(found.group(1)) if found else None
        known = truth(client) if client is not None else {}
        return {"values": {p: known.get(p) for p in asked_paths(text)}}


def _node(schema: dict, path: str) -> dict:
    """The schema node at a ``flatten_schema`` path such as ``owners[].name``."""
    node = schema
    for part in path.replace("[]", ".[]").split("."):
        node = node["items"] if part == "[]" else node["properties"][part]
    return node
