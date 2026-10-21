from pathlib import Path

from rlm_lab.retrieval.corpus import LocalCorpus


def test_local_corpus_ingests_and_chunks() -> None:
    corpus = LocalCorpus.from_directory(Path("tests/fixtures/corpus"))
    assert corpus.chunks
    assert any(chunk.path.endswith(".md") for chunk in corpus.chunks)
    assert all(chunk.chunk_id for chunk in corpus.chunks)


def test_local_corpus_search_ranks_relevant_chunks() -> None:
    corpus = LocalCorpus.from_directory(Path("tests/fixtures/corpus"))
    snapshot = corpus.search("Which system tracks token usage?", top_k=2)

    assert snapshot.chunks
    assert snapshot.chunks[0].score >= snapshot.chunks[-1].score
    assert "token" in snapshot.chunks[0].text.lower()


def test_list_documents_and_filtered_search() -> None:
    corpus = LocalCorpus.from_directory(Path("tests/fixtures/corpus"))

    docs = corpus.list_documents(path_prefix="nested", file_type="md")
    snapshot = corpus.search_with_filters("filter aware retrieval", path_prefix="nested", file_type=".md", top_k=2)

    assert docs
    assert all(doc["path"].startswith("nested") for doc in docs)
    assert all(doc["path"].endswith(".md") for doc in docs)
    assert snapshot.chunks
    assert all(chunk.path.startswith("nested") for chunk in snapshot.chunks)
    assert all(chunk.path.endswith(".md") for chunk in snapshot.chunks)


def test_expand_neighbors_and_document_section() -> None:
    corpus = LocalCorpus.from_directory(Path("tests/fixtures/corpus"), chunk_size=120, chunk_overlap=20)
    doc_chunks = corpus.get_document_chunks("nested/guide.md")

    neighbors = corpus.expand_neighbors(doc_chunks[1].chunk_id, window=1)
    section = corpus.read_document_chunk_range("nested/guide.md", start_index=1, end_index=3)

    assert len(doc_chunks) >= 3
    assert len(neighbors) >= 3
    assert neighbors[1].chunk_id == doc_chunks[1].chunk_id
    assert [chunk.chunk_id for chunk in section] == [doc_chunks[1].chunk_id, doc_chunks[2].chunk_id]
