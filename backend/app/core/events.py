"""
Simple in-process pub/sub used to drive Server-Sent Events per project.

Keeps a short replay buffer per project: if the frontend's EventSource
connects *after* an event has already fired (e.g. the AI call fails or
returns almost instantly, before the browser has finished navigating to
the project page), a new subscriber still receives everything that
happened since project creation instead of the UI hanging forever waiting
for an event that already came and went.
"""
import asyncio
import json
from collections import defaultdict
from typing import Dict, List

MAX_HISTORY_PER_PROJECT = 200


class EventBus:
    def __init__(self):
        self._subscribers: Dict[str, List[asyncio.Queue]] = defaultdict(list)
        self._history: Dict[str, List[str]] = defaultdict(list)

    def subscribe(self, project_id: str, replay: bool = True) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        if replay:
            for payload in self._history.get(project_id, []):
                q.put_nowait(payload)
        self._subscribers[project_id].append(q)
        return q

    def unsubscribe(self, project_id: str, q: asyncio.Queue) -> None:
        if q in self._subscribers.get(project_id, []):
            self._subscribers[project_id].remove(q)

    async def publish(self, project_id: str, event_type: str, data: dict) -> None:
        payload = json.dumps({"type": event_type, "project_id": project_id, "data": data})

        history = self._history[project_id]
        history.append(payload)
        if len(history) > MAX_HISTORY_PER_PROJECT:
            del history[: len(history) - MAX_HISTORY_PER_PROJECT]

        for q in list(self._subscribers.get(project_id, [])):
            await q.put(payload)


event_bus = EventBus()
