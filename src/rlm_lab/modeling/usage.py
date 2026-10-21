from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def sanitize_for_json(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): sanitize_for_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_json(item) for item in value]
    if hasattr(value, "model_dump"):
        return sanitize_for_json(value.model_dump())
    if hasattr(value, "__dict__"):
        return sanitize_for_json(vars(value))
    return repr(value)


def normalize_usage(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, (int, float)):
        return {"total_tokens": int(value)}
    normalized = sanitize_for_json(value)
    if isinstance(normalized, dict):
        return normalized
    return {"raw": normalized}


def merge_usage(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_usage(current, value)
        elif isinstance(current, (int, float)) and isinstance(value, (int, float)):
            merged[key] = current + value
        elif current is None:
            merged[key] = value
        else:
            merged[key] = value
    return merged


def sum_usage(entries: list[dict[str, Any]]) -> dict[str, Any]:
    total: dict[str, Any] = {}
    for entry in entries:
        total = merge_usage(total, normalize_usage(entry))
    return total


def extract_token_counts(usage: dict[str, Any]) -> tuple[int, int, int]:
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens) or 0)
    return prompt_tokens, completion_tokens, total_tokens
