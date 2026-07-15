"""Immutable JSON-compatible value helpers."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from types import MappingProxyType

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | Mapping[str, JsonValue] | tuple[JsonValue, ...]


def freeze_json(value: object) -> JsonValue:
    """Validate and recursively freeze a JSON-compatible value."""
    if isinstance(value, float) and not math.isfinite(value):
        raise TypeError("non-finite numbers are not JSON-compatible")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(freeze_json(item) for item in value)
    raise TypeError(f"value is not JSON-compatible: {type(value).__name__}")


def freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    frozen = freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise TypeError("expected a mapping")
    return frozen


def canonical_json(value: object) -> str:
    """Serialize frozen mappings and tuples into stable canonical JSON."""
    return json.dumps(
        thaw_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def thaw_json(value: object) -> object:
    """Convert immutable JSON containers back to plain dict/list values."""
    if isinstance(value, Mapping):
        return {str(key): thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(child) for child in value]
    return value
