import json
import urllib.error
import urllib.request

import pytest

from whisperr import Whisperr, WhisperrError
from whisperr.transport import Transport


class FakeTransport:
    """Records ops at the client boundary (before wire serialization)."""

    def __init__(self, result="ok"):
        self.result = result
        self.batches = []
        self.identifies = []

    def send_batch(self, events):
        self.batches.append([dict(e) for e in events])
        return self.result

    def send_identify(self, op):
        self.identifies.append(dict(op))
        return self.result


def make_client(transport, **kwargs):
    return Whisperr(
        api_key="wrk_test",
        transport=transport,
        flush_interval=0.05,
        **kwargs,
    )


# ---- client behavior ----

def test_track_delivers_events_with_explicit_user_and_message_id():
    t = FakeTransport()
    w = make_client(t)
    try:
        w.track("user_8842", "payment_failed", {"amount_cents": 4900})
        w.track("user_8842", "subscription_cancelled")
        w.flush()
    finally:
        w.shutdown()

    # Batching is opportunistic, so events may arrive in one or several batches —
    # what matters is that all of them are delivered, correctly shaped.
    events = [e for batch in t.batches for e in batch]
    assert len(events) == 2
    by_type = {e["event_type"]: e for e in events}
    assert by_type["payment_failed"]["external_user_id"] == "user_8842"
    assert by_type["payment_failed"]["properties"] == {"amount_cents": 4900}
    assert by_type["payment_failed"]["occurred_at"].endswith("Z")
    assert len({e["message_id"] for e in events}) == 2


def test_bulk_delivery():
    # All queued events are delivered exactly once (across however many batches).
    t = FakeTransport()
    w = Whisperr(api_key="wrk_test", transport=t, flush_interval=10.0)
    try:
        for i in range(50):
            w.track("user_1", f"event_{i}")
        w.flush()
    finally:
        w.shutdown()
    delivered = [e for batch in t.batches for e in batch]
    assert len(delivered) == 50
    assert len({e["event_type"] for e in delivered}) == 50  # no dupes, none lost


def test_identify_carries_traits_and_channels():
    t = FakeTransport()
    w = make_client(t)
    try:
        w.identify("user_8842", traits={"plan": "pro"}, email="a@b.com", preferred_channel="email")
        w.flush()
    finally:
        w.shutdown()

    assert len(t.identifies) == 1
    op = t.identifies[0]
    assert op["external_user_id"] == "user_8842"
    assert op["traits"] == {"plan": "pro"}
    assert op["preferred_channel"] == "email"
    assert op["channels"] == [{"type": "email", "address": "a@b.com", "opted_in": True}]


def test_track_requires_user_and_event_type():
    t = FakeTransport()
    w = make_client(t)
    try:
        w.track("", "payment_failed")
        w.track("user_1", "")
        w.flush()
    finally:
        w.shutdown()
    assert t.batches == []


def test_auth_failure_emits_and_stops():
    t = FakeTransport(result="auth")
    errors = []
    w = make_client(t, on_error=errors.append)
    try:
        w.track("user_1", "feature_used")
        w.flush()
    finally:
        w.shutdown()
    assert any(e.type == "auth" for e in errors)


def test_drop_on_4xx():
    t = FakeTransport(result="drop")
    errors = []
    w = make_client(t, on_error=errors.append)
    try:
        w.track("user_1", "feature_used")
        w.flush()
    finally:
        w.shutdown()
    assert any(e.type == "dropped" for e in errors)


def test_retry_exhausted_is_bounded():
    t = FakeTransport(result="retry")
    errors = []
    w = make_client(t, on_error=errors.append, max_retries=0)
    try:
        w.track("user_1", "feature_used")
        w.flush()
    finally:
        w.shutdown()
    assert any(e.type == "retry_exhausted" for e in errors)


def test_failed_events_are_retained_and_retried():
    # A delivery that fails on auth must keep the events buffered so a later
    # flush retries the SAME events — they must not be silently dropped.
    t = FakeTransport(result="auth")
    # High flush_interval so only our explicit flush() calls drive delivery.
    w = Whisperr(api_key="wrk_test", transport=t, flush_interval=3600)
    try:
        w.track("user_1", "feature_used")
        w.flush()  # auth → event retained, not delivered
        attempts_before = len(t.batches)

        t.result = "ok"
        w.flush()  # retries the retained event, now succeeds
        attempts_after = len(t.batches)
    finally:
        w.shutdown()

    # The event was re-sent after the failure was fixed (retained), not dropped.
    assert attempts_after == attempts_before + 1
    assert [e["event_type"] for e in t.batches[-1]] == ["feature_used"]


def test_disabled_is_noop():
    t = FakeTransport()
    w = Whisperr(api_key="wrk_test", transport=t, disabled=True)
    assert w.ready is False
    w.track("user_1", "feature_used")
    w.flush()
    w.shutdown()
    assert t.batches == [] and t.identifies == []


def test_missing_api_key_raises():
    with pytest.raises(ValueError):
        Whisperr(api_key="")


# ---- transport wire mapping ----

def _capture_urlopen(captured):
    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = json.loads(req.data.decode())
        return FakeResp()

    return fake


def test_transport_batch_wire_shape(monkeypatch):
    captured = {}
    monkeypatch.setattr(urllib.request, "urlopen", _capture_urlopen(captured))
    t = Transport("https://api.whisperr.net/", "wrk_test", 10, lambda m: None)
    result = t.send_batch(
        [{"external_user_id": "u1", "event_type": "x", "occurred_at": "2026-01-01T00:00:00.000Z",
          "properties": {"a": 1}, "context": {"k": "v"}, "message_id": "mid1"}]
    )
    assert result == "ok"
    assert captured["url"] == "https://api.whisperr.net/v1/events/batch"
    assert captured["headers"]["x-api-key"] == "wrk_test"
    ev = captured["body"]["events"][0]
    assert ev["external_user_id"] == "u1"
    assert ev["context"] == {"k": "v", "$message_id": "mid1"}


def test_transport_identify_channel_mapping(monkeypatch):
    captured = {}
    monkeypatch.setattr(urllib.request, "urlopen", _capture_urlopen(captured))
    t = Transport("https://api.whisperr.net", "wrk_test", 10, lambda m: None)
    t.send_identify(
        {"external_user_id": "u1", "traits": {"plan": "pro"},
         "channels": [{"type": "email", "address": "a@b.com", "opted_in": True}]}
    )
    assert captured["url"] == "https://api.whisperr.net/v1/identify"
    assert captured["body"]["channels"] == [{"channel": "email", "address": "a@b.com", "opted_in": True}]


@pytest.mark.parametrize("code,expected", [(401, "auth"), (403, "auth"), (429, "retry"), (500, "retry"), (400, "drop"), (422, "drop")])
def test_transport_status_classification(monkeypatch, code, expected):
    def raiser(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "err", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", raiser)
    t = Transport("https://api.whisperr.net", "wrk_test", 10, lambda m: None)
    assert t.send_batch([{"external_user_id": "u1", "event_type": "x",
                          "occurred_at": "2026-01-01T00:00:00.000Z", "message_id": "m"}]) == expected
