"""Async event bus for SSE streaming of pipeline progress."""

import asyncio
import json
import logging
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

DEFAULT_MAX_SIZE = 1000


class EventBus:
    """
    Per-request async event bus with backpressure.

    Producers call `emit(event_type, data)` to publish events.
    The SSE endpoint calls `stream()` to get a generator of SSE-formatted strings.
    Call `close()` when the pipeline is done to signal end-of-stream.
    """

    def __init__(self, max_size: int = DEFAULT_MAX_SIZE):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self._closed = False

    def emit(self, event_type: str, data: Any) -> None:
        """Publish an event. Safe to call from sync or async context."""
        if self._closed:
            return
        payload = {"event_type": event_type, "data": data}
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning(f"EventBus queue full, dropping event: {event_type}")

    def close(self) -> None:
        """Signal that no more events will be emitted."""
        if not self._closed:
            self._closed = True
            try:
                self._queue.put_nowait(None)  # sentinel
            except asyncio.QueueFull:
                pass

    async def stream(self) -> AsyncIterator[str]:
        """
        Yield SSE-formatted strings until the bus is closed.

        SSE format:  data: <json>\n\n
        """
        while True:
            item = await self._queue.get()
            if item is None:
                yield "data: {\"event_type\": \"stream_end\"}\n\n"
                break
            try:
                yield f"data: {json.dumps(item)}\n\n"
            except (TypeError, ValueError) as e:
                logger.error(f"Failed to serialize event: {e}")
