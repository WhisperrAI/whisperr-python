from __future__ import annotations

import atexit
import queue
import random
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from .errors import WhisperrError
from .transport import Transport

DEFAULT_BASE = "https://api.whisperr.net"
# Mirrors the server's accepted event_type shape.
_SNAKE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
# Sentinel pushed onto the queue to wake + stop the consumer thread.
_SHUTDOWN = object()

Channel = Dict[str, Any]
OnError = Callable[[WhisperrError], None]


class Whisperr:
    """A Whisperr client for a Python backend.

    Hold one instance per process and call :meth:`shutdown` before exit (also
    registered via ``atexit``). ``track``/``identify`` are non-blocking: they
    enqueue and a background thread batches + delivers with retries.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE,
        flush_at: int = 20,
        flush_interval: float = 10.0,
        max_queue_size: int = 10000,
        max_batch_size: int = 500,
        max_retries: int = 6,
        request_timeout: float = 10.0,
        disabled: bool = False,
        debug: bool = False,
        on_error: Optional[OnError] = None,
        transport: Optional[Transport] = None,
    ) -> None:
        if not api_key and not disabled:
            raise ValueError("api_key is required")
        self._muted = bool(disabled)
        self._flush_at = flush_at
        self._max_batch = min(max_batch_size, 500)
        self._max_retries = max_retries
        self._debug = debug
        self._on_error = on_error
        self._flush_interval = flush_interval
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize=max_queue_size)
        self._transport = transport or Transport(base_url, api_key, request_timeout, self._warn)
        self._running = not self._muted
        self._thread: Optional[threading.Thread] = None

        if not self._muted:
            self._thread = threading.Thread(target=self._run, name="whisperr", daemon=True)
            self._thread.start()
            atexit.register(self.shutdown)

    @property
    def ready(self) -> bool:
        return not self._muted

    def identify(
        self,
        external_user_id: str,
        *,
        traits: Optional[Dict[str, Any]] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        push_token: Optional[str] = None,
        preferred_channel: Optional[str] = None,
        channels: Optional[Sequence[Channel]] = None,
    ) -> None:
        """Associate traits/contact channels with a user. ``external_user_id``
        is always explicit — pass the same id you use in :meth:`track`."""
        if self._muted or not external_user_id:
            return
        self._enqueue(
            {
                "kind": "identify",
                "external_user_id": external_user_id,
                "traits": traits,
                "preferred_channel": preferred_channel,
                "channels": list(channels)
                if channels is not None
                else _build_channels(email, phone, push_token),
            }
        )

    def track(
        self,
        external_user_id: str,
        event_type: str,
        properties: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a product event for a known user."""
        if self._muted or not external_user_id or not event_type:
            return
        if self._debug and not _SNAKE.match(event_type):
            self._warn(f'event_type "{event_type}" is not snake_case — the server will reject it')
        self._enqueue(
            {
                "kind": "track",
                "external_user_id": external_user_id,
                "event_type": event_type,
                "properties": properties,
                "context": context,
                "occurred_at": _now_iso(),
                "message_id": uuid.uuid4().hex,
            }
        )

    def flush(self) -> None:
        """Block until everything currently queued has been delivered (or dropped)."""
        if self._muted:
            return
        self._queue.join()

    def shutdown(self) -> None:
        """Stop the background thread after delivering what's queued. Idempotent."""
        if self._muted or not self._running:
            return
        self._running = False
        self._put_sentinel()
        if self._thread is not None:
            self._thread.join()

    # ---- internals ----

    def _enqueue(self, op: Dict[str, Any]) -> None:
        while True:
            try:
                self._queue.put_nowait(op)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                    self._emit("dropped", "queue full — dropped 1 oldest event")
                except queue.Empty:
                    pass  # drained concurrently; loop and retry the put

    def _put_sentinel(self) -> None:
        try:
            self._queue.put_nowait(_SHUTDOWN)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self._queue.put_nowait(_SHUTDOWN)
            except (queue.Empty, queue.Full):
                pass

    def _run(self) -> None:
        while self._running or not self._queue.empty():
            try:
                first = self._queue.get(timeout=self._flush_interval)
            except queue.Empty:
                continue
            if first is _SHUTDOWN:
                self._queue.task_done()
                continue
            items = [first]
            while len(items) < self._max_batch:
                try:
                    nxt = self._queue.get_nowait()
                except queue.Empty:
                    break
                if nxt is _SHUTDOWN:
                    self._queue.task_done()
                    self._running = False
                    break
                items.append(nxt)
            self._deliver_items(items)

    def _deliver_items(self, items: List[Dict[str, Any]]) -> None:
        tracks = [x for x in items if x["kind"] == "track"]
        idents = [x for x in items if x["kind"] == "identify"]
        try:
            if tracks:
                self._deliver(lambda: self._transport.send_batch(tracks), len(tracks))
            for op in idents:
                self._deliver(lambda op=op: self._transport.send_identify(op), 1)
        finally:
            for _ in items:
                self._queue.task_done()

    def _deliver(self, send: Callable[[], str], count: int) -> None:
        retries = 0
        while True:
            result = send()
            if result == "ok":
                return
            if result == "drop":
                self._emit("dropped", f"dropped {count} event(s) — rejected by server")
                return
            if result == "auth":
                self._emit("auth", "delivery paused — API key rejected", 401)
                return
            retries += 1
            # Stop early when shutting down so exit isn't held up by backoff.
            if retries > self._max_retries or not self._running:
                self._emit("retry_exhausted", "delivery failed after retries")
                return
            time.sleep(_backoff(retries))

    def _emit(self, type_: str, message: str, status: Optional[int] = None) -> None:
        if not self._on_error:
            return
        try:
            self._on_error(WhisperrError(type=type_, message=message, status=status))
        except Exception:
            pass  # host callback threw — ignore

    def _warn(self, msg: str) -> None:
        if self._debug:
            print(f"[whisperr] {msg}", file=sys.stderr)


def _build_channels(
    email: Optional[str], phone: Optional[str], push_token: Optional[str]
) -> Optional[List[Channel]]:
    out: List[Channel] = []
    if email:
        out.append({"type": "email", "address": email, "opted_in": True})
    if phone:
        out.append({"type": "sms", "address": phone, "opted_in": True})
    if push_token:
        out.append({"type": "push", "address": push_token, "opted_in": True})
    return out or None


def _backoff(attempt: int) -> float:
    base = min(30.0, 1.0 * (2 ** attempt))
    return base + random.random() * 0.25


def _now_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
