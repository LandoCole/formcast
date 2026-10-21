from rlm_lab.modeling.usage import extract_token_counts, merge_usage, normalize_usage


def test_usage_normalization_and_merge() -> None:
    left = normalize_usage({"prompt_tokens": 10, "completion_tokens": 5, "details": {"cached_tokens": 2}})
    right = normalize_usage({"prompt_tokens": 7, "completion_tokens": 3, "details": {"cached_tokens": 1}})
    merged = merge_usage(left, right)

    assert merged["prompt_tokens"] == 17
    assert merged["completion_tokens"] == 8
    assert merged["details"]["cached_tokens"] == 3


def test_extract_token_counts_uses_total_when_present() -> None:
    prompt_tokens, completion_tokens, total_tokens = extract_token_counts(
        {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 11}
    )
    assert (prompt_tokens, completion_tokens, total_tokens) == (4, 6, 11)
