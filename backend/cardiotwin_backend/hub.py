# WebSocket hub: apps subscribe to a patient and receive "status", "alert", "emergency" and "reading" messages.
# Safe to call from the evaluation thread: publish() hands the message to the event loop.
from __future__ import annotations

import asyncio
import json
import threading
from collections import defaultdict
from typing import Any, Callable, Optional

from fastapi import WebSocket

Shape = Optional[Callable[[dict], Optional[dict]]]     # per-subscriber view of a message (role-based), None = drop


class Hub:
    def __init__(self):
        self._subs: dict[str, dict[WebSocket, Shape]] = defaultdict(dict)
        self._lock = threading.Lock()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.sent: list[dict] = []          # last messages, for tests and the debug endpoint
        self.max_kept = 200

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def subscribe(self, patient_id: str, ws: WebSocket, shape: Shape = None) -> None:
        with self._lock:
            self._subs[patient_id][ws] = shape

    def unsubscribe(self, patient_id: str, ws: WebSocket) -> None:
        with self._lock:
            self._subs[patient_id].pop(ws, None)

    def subscribers(self, patient_id: str) -> int:
        with self._lock:
            return len(self._subs.get(patient_id, ()))

    def publish(self, patient_id: str, msg: dict[str, Any]) -> int:
        """Queue a message for every subscriber of the patient; returns how many sockets it was queued for."""
        msg = {"patient_id": patient_id, **msg}
        self.sent.append(msg)
        del self.sent[:-self.max_kept]
        with self._lock:
            targets = list(self._subs.get(patient_id, {}).items())
        if not targets or self.loop is None or self.loop.is_closed():
            return 0
        n = 0
        for ws, shape in targets:
            m = shape(msg) if shape else msg
            if m is None:
                continue
            n += 1
            coro = self._send(patient_id, ws, json.dumps(m, default=str))
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is self.loop:
                self.loop.create_task(coro)
            else:
                asyncio.run_coroutine_threadsafe(coro, self.loop)
        return n

    async def _send(self, patient_id: str, ws: WebSocket, text: str) -> None:
        try:
            await ws.send_text(text)
        except Exception:
            self.unsubscribe(patient_id, ws)
