from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

from rlm_lab.execution.events import RetrievedChunk, RetrievalSnapshot


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")
SUPPORTED_SUFFIXES = {".txt", ".md", ".json"}


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]


class LocalCorpus:
    def __init__(self, root: Path, chunks: list[RetrievedChunk]):
        self.root = root
        self.chunks = chunks
        self._chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        self._chunks_by_path: dict[str, list[RetrievedChunk]] = defaultdict(list)
        self._chunk_position_by_id: dict[str, tuple[str, int]] = {}
        for chunk in chunks:
            path_chunks = self._chunks_by_path[chunk.path]
            self._chunk_position_by_id[chunk.chunk_id] = (chunk.path, len(path_chunks))
            path_chunks.append(chunk)
        self._doc_tokens = [tokenize(chunk.text) for chunk in chunks]
        self._doc_lengths = [len(tokens) or 1 for tokens in self._doc_tokens]
        self._average_doc_length = sum(self._doc_lengths) / len(self._doc_lengths) if self._doc_lengths else 1.0
        self._doc_freq = defaultdict(int)
        for tokens in self._doc_tokens:
            for term in set(tokens):
                self._doc_freq[term] += 1

    @classmethod
    def from_directory(
        cls,
        root: Path,
        *,
        chunk_size: int = 900,
        chunk_overlap: int = 150,
    ) -> "LocalCorpus":
        root = root.expanduser().resolve()
        if not root.exists():
            raise FileNotFoundError(f"Corpus directory does not exist: {root}")

        chunks: list[RetrievedChunk] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            text = cls._read_text(path)
            for start, end, chunk_text in cls._chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap):
                digest = hashlib.sha1(f"{path.relative_to(root)}:{start}:{end}".encode("utf-8")).hexdigest()[:12]
                preview = chunk_text.strip().replace("\n", " ")[:240]
                chunks.append(
                    RetrievedChunk(
                        chunk_id=digest,
                        path=str(path.relative_to(root)),
                        preview=preview,
                        score=0.0,
                        start_offset=start,
                        end_offset=end,
                        text=chunk_text,
                    )
                )
        return cls(root=root, chunks=chunks)

    @staticmethod
    def _read_text(path: Path) -> str:
        raw = path.read_text(encoding="utf-8")
        if path.suffix.lower() != ".json":
            return raw
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        return json.dumps(parsed, indent=2, sort_keys=True)

    @staticmethod
    def _chunk_text(text: str, *, chunk_size: int, chunk_overlap: int) -> Iterable[tuple[int, int, str]]:
        if len(text) <= chunk_size:
            yield 0, len(text), text
            return

        cursor = 0
        while cursor < len(text):
            end = min(len(text), cursor + chunk_size)
            yield cursor, end, text[cursor:end]
            if end >= len(text):
                break
            cursor = max(end - chunk_overlap, cursor + 1)

    def overview(self) -> str:
        files = sorted({chunk.path for chunk in self.chunks})
        lines = [
            f"Corpus root: {self.root}",
            f"Files indexed: {len(files)}",
            f"Chunks indexed: {len(self.chunks)}",
        ]
        if files:
            lines.append("Files:")
            lines.extend(f"- {path}" for path in files[:20])
        return "\n".join(lines)

    def get_chunk(self, chunk_id: str) -> RetrievedChunk:
        if chunk_id not in self._chunk_map:
            raise KeyError(f"Unknown chunk id: {chunk_id}")
        return self._chunk_map[chunk_id]

    def get_document_chunks(self, path: str) -> list[RetrievedChunk]:
        normalized = path.strip()
        if normalized not in self._chunks_by_path:
            raise KeyError(f"Unknown document path: {path}")
        return list(self._chunks_by_path[normalized])

    def list_documents(
        self,
        *,
        path_prefix: str | None = None,
        file_type: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        documents = []
        prefix = (path_prefix or "").strip()
        normalized_file_type = self._normalize_file_type(file_type)
        for path, chunks in sorted(self._chunks_by_path.items()):
            if prefix and not path.startswith(prefix):
                continue
            if normalized_file_type and not path.endswith(normalized_file_type):
                continue
            documents.append(
                {
                    "path": path,
                    "chunk_count": len(chunks),
                    "char_count": sum(len(chunk.text) for chunk in chunks),
                    "preview": chunks[0].preview if chunks else "",
                }
            )
            if len(documents) >= limit:
                break
        return documents

    def expand_neighbors(self, chunk_id: str, *, window: int = 1) -> list[RetrievedChunk]:
        path, index = self._chunk_position_by_id.get(chunk_id, (None, None))
        if path is None or index is None:
            raise KeyError(f"Unknown chunk id: {chunk_id}")
        chunks = self._chunks_by_path[path]
        start = max(0, index - max(0, window))
        end = min(len(chunks), index + max(0, window) + 1)
        return [chunks[offset].model_copy(update={"score": chunks[offset].score}) for offset in range(start, end)]

    def read_document_chunk_range(self, path: str, *, start_index: int = 0, end_index: int = 1) -> list[RetrievedChunk]:
        chunks = self.get_document_chunks(path)
        if not chunks:
            return []
        start = max(0, start_index)
        end = min(len(chunks), max(start + 1, end_index))
        return chunks[start:end]

    def search(self, query: str, *, top_k: int = 4) -> RetrievalSnapshot:
        return self.search_with_filters(query, top_k=top_k)

    def search_with_filters(
        self,
        query: str,
        *,
        top_k: int = 4,
        path_prefix: str | None = None,
        file_type: str | None = None,
    ) -> RetrievalSnapshot:
        if not self.chunks:
            return RetrievalSnapshot(query=query, top_k=top_k, chunks=[])

        query_terms = tokenize(query)
        scores: list[tuple[float, RetrievedChunk]] = []
        normalized_prefix = (path_prefix or "").strip()
        normalized_file_type = self._normalize_file_type(file_type)
        for index, chunk in enumerate(self.chunks):
            if normalized_prefix and not chunk.path.startswith(normalized_prefix):
                continue
            if normalized_file_type and not chunk.path.endswith(normalized_file_type):
                continue
            score = self._bm25_score(query_terms, index)
            if score > 0:
                scores.append((score, chunk))

        scores.sort(key=lambda item: item[0], reverse=True)
        ranked = [
            chunk.model_copy(update={"score": score})
            for score, chunk in scores[:top_k]
        ]
        return RetrievalSnapshot(query=query, top_k=top_k, chunks=ranked)

    def format_context(self, chunks: list[RetrievedChunk]) -> str:
        if not chunks:
            return ""
        parts = []
        for chunk in chunks:
            parts.append(
                f"[{chunk.chunk_id}] {chunk.path} ({chunk.start_offset}:{chunk.end_offset})\n{chunk.text.strip()}"
            )
        return "\n\n".join(parts)

    def _bm25_score(self, query_terms: list[str], index: int) -> float:
        if not query_terms:
            return 0.0
        k1 = 1.5
        b = 0.75
        doc_terms = self._doc_tokens[index]
        term_freq = Counter(doc_terms)
        doc_length = self._doc_lengths[index]
        score = 0.0
        corpus_size = len(self.chunks)

        for term in query_terms:
            freq = term_freq.get(term, 0)
            if freq == 0:
                continue
            df = self._doc_freq.get(term, 0)
            idf = math.log(1 + (corpus_size - df + 0.5) / (df + 0.5))
            numerator = freq * (k1 + 1)
            denominator = freq + k1 * (1 - b + b * (doc_length / self._average_doc_length))
            score += idf * (numerator / denominator)
        return score

    @staticmethod
    def _normalize_file_type(file_type: str | None) -> str:
        if not file_type:
            return ""
        normalized = file_type.strip().lower()
        if not normalized:
            return ""
        return normalized if normalized.startswith(".") else f".{normalized}"
