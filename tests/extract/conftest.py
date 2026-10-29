"""Fixtures for the extraction tests."""

from pathlib import Path

import pytest

from tests.extract.corpus import ROTATE, SHIFT, Client, clients, make_form, other_form

#: How many of the corpus folder's PDFs are filled copies of the client form (the rest are another form).
CORPUS_FILLED = 20


def write_corpus(folder: Path) -> Path:
    """Write the corpus folder's 22 PDFs into ``folder`` (made if needed) and return it; see ``corpus_dir``."""
    folder.mkdir(parents=True, exist_ok=True)
    people = clients(CORPUS_FILLED)
    pdfs: list[bytes] = []
    for i, client in enumerate(people):
        if i < 12:
            pdfs.append(make_form(client, kind="fillable"))
        elif i < 18:
            pdfs.append(make_form(client, kind="flattened"))
        elif i == 18:
            pdfs.append(make_form(client, kind="flattened", shift=SHIFT))
        else:
            pdfs.append(make_form(client, kind="fillable", rotate=ROTATE))
    pdfs += [other_form(), other_form()]
    for n, data in enumerate(pdfs, start=1):
        (folder / f"client_{n:05d}.pdf").write_bytes(data)
    return folder


def corpus_clients(folder: Path) -> list[tuple[Path, Client | None]]:
    """Each PDF ``write_corpus`` wrote into ``folder``, in order, with its client (``None`` for the other form)."""
    people = clients(CORPUS_FILLED)
    files = sorted(folder.glob("client_*.pdf"))
    return [(f, people[n] if n < len(people) else None) for n, f in enumerate(files)]


@pytest.fixture
def corpus_dir(tmp_path: Path) -> Path:
    """A folder of 22 PDFs: 12 fillable, 6 flattened, 1 shifted flattened and 1 rotated fillable filled form,
    then 2 copies of a different form, named ``client_00001.pdf`` ...  The filled ones are ``clients(20)`` in order."""
    return write_corpus(tmp_path / "corpus")
