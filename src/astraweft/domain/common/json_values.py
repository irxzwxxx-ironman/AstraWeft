"""Immutable JSON-compatible values without infrastructure dependencies."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from types import MappingProxyType


def freeze_json(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        raise TypeError("non-finite values are not valid JSON")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(freeze_json(item) for item in value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    frozen = freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise TypeError("expected a mapping")
    return frozen
