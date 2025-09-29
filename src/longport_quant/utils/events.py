"""Very light pub-sub event bus for in-process communication."""

from collections import defaultdict
from typing import Awaitable, Callable, DefaultDict

Subscriber = Callable[[dict], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: DefaultDict[str, list[Subscriber]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Subscriber) -> None:
        self._subscribers[topic].append(handler)

    def unsubscribe(self, topic: str, handler: Subscriber) -> None:
        handlers = self._subscribers.get(topic, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, topic: str, payload: dict) -> None:
        for handler in list(self._subscribers.get(topic, [])):
            await handler(payload)

