"""build_setup with a real model: the AI start on five corpus samples, then the whole corpus folder read."""

from __future__ import annotations

import pytest

from formcast.errors import FormcastError
from formcast.extract.read import extract
from formcast.extract.samples import SamplePdf
from formcast.extract.setup import SetupSpec, build_setup
from formcast.llm import LLMConfig
from formcast.pagemap.build import DetectPolicy
from tests.extract.conftest import corpus_clients, write_corpus
from tests.extract.corpus import assert_no_wrong_found

#: The corpus folder's samples: two fillable, two flattened and the flattened copy pushed down the page.
SAMPLE_INDEXES = (0, 1, 12, 13, 18)


@pytest.mark.llm
def test_a_real_model_builds_a_template_that_reads_the_corpus_with_no_wrong_value(eval_env, tmp_path):
    try:
        config = LLMConfig.from_env("ANTHROPIC_EVAL")
    except FormcastError:
        pytest.skip("ANTHROPIC_EVAL_* not configured")
    files = corpus_clients(write_corpus(tmp_path / "corpus"))
    samples = [SamplePdf(files[n][0].name, files[n][0].read_bytes()) for n in SAMPLE_INDEXES]
    result = build_setup(
        samples, SetupSpec(names_from_titles=False, name="Client info"), config, detectors={}, policy=DetectPolicy(digital=(), scanned=())
    )
    print(f"\n{result.proof.ready} of {result.proof.total} fields ready; usage {result.usage}")
    for warning in result.warnings:
        print(f"warning: {warning}")
    for f, client in files:
        assert_no_wrong_found(extract(result.template, f, file=f.name), client)
