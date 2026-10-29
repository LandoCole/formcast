"""Fixtures for the extraction tests."""

from pathlib import Path

import pytest

from tests.extract.corpus import ROTATE, SHIFT, clients, make_form, other_form


@pytest.fixture
def corpus_dir(tmp_path: Path) -> Path:
    """A folder of 22 PDFs: 12 fillable, 6 flattened, 1 shifted flattened and 1 rotated fillable filled form,
    then 2 copies of a different form, named ``client_00001.pdf`` ...  The filled ones are ``clients(20)`` in order."""
    folder = tmp_path / "corpus"
    folder.mkdir()
    people = clients(20)
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
