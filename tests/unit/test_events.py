"""In-process application event tests."""

from dataclasses import dataclass

import pytest

from astraweft.application.events import EventBus


@dataclass(frozen=True)
class SomethingHappened:
    value: int


@pytest.mark.asyncio
async def test_event_bus_runs_sync_and_async_handlers_in_order() -> None:
    bus = EventBus()
    received: list[str] = []

    def sync_handler(event: SomethingHappened) -> None:
        received.append(f"sync:{event.value}")

    async def async_handler(event: SomethingHappened) -> None:
        received.append(f"async:{event.value}")

    unsubscribe = bus.subscribe(SomethingHappened, sync_handler)
    bus.subscribe(SomethingHappened, async_handler)

    await bus.publish(SomethingHappened(7))
    unsubscribe()
    unsubscribe()
    await bus.publish(SomethingHappened(8))

    assert received == ["sync:7", "async:7", "async:8"]


@pytest.mark.asyncio
async def test_event_bus_ignores_unsubscribed_event_types() -> None:
    bus = EventBus()

    await bus.publish(SomethingHappened(1))
