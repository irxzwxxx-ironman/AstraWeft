"""Small in-process event bus for post-transaction UI updates."""

from __future__ import annotations

import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TypeVar, cast

EventT = TypeVar("EventT")
EventHandler = Callable[[EventT], Awaitable[None] | None]


class EventBus:
    """Publish typed events in subscription order.

    The bus is not a durable queue and must never be the only source of truth.
    """

    def __init__(self) -> None:
        self._handlers: dict[type[object], list[EventHandler[object]]] = defaultdict(list)

    def subscribe(
        self,
        event_type: type[EventT],
        handler: EventHandler[EventT],
    ) -> Callable[[], None]:
        handlers = self._handlers[event_type]
        object_handler = cast(EventHandler[object], handler)
        handlers.append(object_handler)

        def unsubscribe() -> None:
            if object_handler in handlers:
                handlers.remove(object_handler)

        return unsubscribe

    async def publish(self, event: object) -> None:
        for handler in tuple(self._handlers[type(event)]):
            result = handler(event)
            if inspect.isawaitable(result):
                await result
