from __future__ import annotations

import atexit
import random
import re
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence

from .errors import WhisperrError
from .transport import Transport

DEFAULT_BASE = "https://api.whisperr.net"
# Mirrors the server's accepted event_type shape.
_SNAKE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")

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
        self._max_queue = max_queue_size
        self._max_retries = max_retries
        self._debug = debug
        self._on_error = on_error
        self._flush_interval = flush_interval
        self._transport = transport or Transport(base_url, api_key, request_timeout, self._warn)

        # Ordered in-memory buffer. A drain pass takes a batch off the front,
        # delivers it, and only puts it back (at the front) if delivery gives up —
        # so a failed flush *retains* events for the next flush instead of
        # dropping them. There's no durable store: a process crash loses unsent
        # events, which is an acceptable trade for zero I/O on the hot path.
        self._buf: Deque[Dict[str, Any]] = deque()
        self._buf_lock = threading.Lock()
        # Serializes delivery so the background thread and an explicit flush()
        # never send concurrently (and never fight over the same batch).
        self._deliver_lock = threading.Lock()
        self._wake = threading.Event()

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
        event_type = event_type.strip()
        if not event_type:
            return
        if not _SNAKE.match(event_type):
            self._emit("dropped", f'invalid event_type "{event_type}" — expected snake_case')
            self._warn(f'invalid event_type "{event_type}" — event was not queued')
            return
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
        """Best-effort barrier: deliver everything currently buffered, blocking
        until the buffer drains or delivery can't proceed (auth/transport
        failure). Events that can't be delivered stay buffered for the next
        flush rather than being dropped."""
        if self._muted:
            return
        self._drain_once()

    def shutdown(self) -> None:
        """Stop the background thread after a final delivery pass. Idempotent."""
        if self._muted or not self._running:
            return
        self._running = False
        self._wake.set()
        if self._thread is not None:
            self._thread.join()

    # ---- internals ----

    def _enqueue(self, op: Dict[str, Any]) -> None:
        dropped = 0
        with self._buf_lock:
            self._buf.append(op)
            while len(self._buf) > self._max_queue:
                self._buf.popleft()
                dropped += 1
            size = len(self._buf)
        if dropped:
            self._emit("dropped", f"queue full — dropped {dropped} oldest event(s)")
        # Wake the worker to flush early once enough has accumulated.
        if size >= self._flush_at:
            self._wake.set()

    def _run(self) -> None:
        # Periodic + size-triggered delivery. Always makes one final pass after
        # shutdown so the loop can't spin on an undeliverable (retained) batch.
        while True:
            self._wake.wait(timeout=self._flush_interval)
            self._wake.clear()
            try:
                self._drain_once()
            except Exception:
                pass  # never let the worker thread die on a delivery error
            if not self._running:
                return

    def _take_batch(self) -> List[Dict[str, Any]]:
        """Remove and return the next batch from the front of the buffer.

        An identify is sent on its own; track ops are grouped into a leading run
        (up to ``max_batch``). Returns ``[]`` when the buffer is empty."""
        with self._buf_lock:
            if not self._buf:
                return []
            if self._buf[0]["kind"] == "identify":
                return [self._buf.popleft()]
            batch: List[Dict[str, Any]] = []
            while self._buf and len(batch) < self._max_batch and self._buf[0]["kind"] == "track":
                batch.append(self._buf.popleft())
            return batch

    def _requeue_front(self, batch: List[Dict[str, Any]]) -> None:
        with self._buf_lock:
            self._buf.extendleft(reversed(batch))

    def _drain_once(self) -> None:
        # One delivery pass, serialized against the background thread.
        with self._deliver_lock:
            while True:
                batch = self._take_batch()
                if not batch:
                    return
                result = self._deliver(batch)
                if result in ("ok", "drop"):
                    continue
                # auth / retry_exhausted: hand the batch back to the front and
                # stop; a later flush retries from where we left off.
                self._requeue_front(batch)
                return

    def _deliver(self, batch: List[Dict[str, Any]]) -> str:
        if batch[0]["kind"] == "identify":
            send: Callable[[], str] = lambda: self._transport.send_identify(batch[0])
            count = 1
        else:
            send = lambda: self._transport.send_batch(batch)
            count = len(batch)
        retries = 0
        while True:
            result = send()
            if result == "ok":
                return "ok"
            if result == "drop":
                self._emit("dropped", f"dropped {count} event(s) — rejected by server")
                return "drop"
            if result == "auth":
                self._emit("auth", "delivery paused — API key rejected", 401)
                return "auth"
            retries += 1
            # Stop early when shutting down so exit isn't held up by backoff.
            if retries > self._max_retries or not self._running:
                self._emit("retry_exhausted", "delivery failed after retries; will retry on next flush")
                return "retry_exhausted"
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
