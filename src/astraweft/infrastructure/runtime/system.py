"""System clock and monotonic UUID v7 generation."""

from __future__ import annotations

import secrets
import threading
import time
from datetime import UTC, datetime
from uuid import UUID

from astraweft.ports.runtime import Clock

_MAX_TIMESTAMP_MS = (1 << 48) - 1
_RANDOM_BITS = 74
_MAX_RANDOM = (1 << _RANDOM_BITS) - 1


class SystemClock:
    """Production UTC and duration clock."""

    def now(self) -> datetime:
        return datetime.now(tz=UTC)

    def monotonic(self) -> float:
        return time.monotonic()


class UUID7Generator:
    """RFC 9562 UUID v7 generator monotonic within one process.

    Backward wall-clock movement retains the last timestamp and increments the
    74 random bits, preserving sortable identifiers without database coupling.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._last_timestamp_ms = -1
        self._random_sequence = 0

    def new(self) -> str:
        with self._lock:
            timestamp_ms = int(self._clock.now().timestamp() * 1000)
            if not 0 <= timestamp_ms <= _MAX_TIMESTAMP_MS:
                raise OverflowError("clock is outside the UUID v7 timestamp range")

            if timestamp_ms > self._last_timestamp_ms:
                self._last_timestamp_ms = timestamp_ms
                self._random_sequence = secrets.randbits(_RANDOM_BITS)
            else:
                timestamp_ms = self._last_timestamp_ms
                self._random_sequence = (self._random_sequence + 1) & _MAX_RANDOM
                if self._random_sequence == 0:
                    raise OverflowError("UUID v7 sequence exhausted within one millisecond")

            random_a = self._random_sequence >> 62
            random_b = self._random_sequence & ((1 << 62) - 1)
            value = (
                (timestamp_ms << 80) | (0b0111 << 76) | (random_a << 64) | (0b10 << 62) | random_b
            )
            return str(UUID(int=value))
