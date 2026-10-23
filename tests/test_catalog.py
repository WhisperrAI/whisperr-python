import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from whisperr import CatalogClient, Whisperr, WhisperrCatalogError, WhisperrConfigurationError, to_minor_units
from whisperr.catalog import MAX_BODY_BYTES, parse_retry_after


class Scripted:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        parsed = None if body is None else json.loads(body)
        self.calls.append({"method": method, "url": url, "headers": headers, "raw": body, "body": parsed})
        r = self.replies(parsed) if callable(self.replies) else self.replies[len(self.calls) - 1]
        if isinstance(r, Exception):
            raise r
        status, payload, headers = (r + ({},))[:3] if len(r) == 2 else r
        return status, headers, b"" if payload is None else json.dumps(payload).encode()


def ok(n):
    return (200, {"accepted": n, "applied": n, "unchanged": 0, "ignored_older": 0, "rejected": []})


def item(sid, **extra):
    d = {"source_item_id": sid, "type": "product", "name": "Item " + sid, "availability": "available",
         "updated_at": "2026-10-23T09:00:00Z"}
    d.update(extra)
    return d


def client(http, **kw):
    kw.setdefault("sleep", lambda s: None)
    return CatalogClient("wrk_x", base_url="https://api.test", http=http, **kw)


def test_publishable_key_is_refused_from_the_main_client():
    w = Whisperr(api_key="wpk_live_x", flush_interval=60)
    try:
        with pytest.raises(WhisperrConfigurationError):
            w.catalog.upsert([item("a")])
        with pytest.raises(WhisperrConfigurationError):
            w.catalog.status()
    finally:
        w.shutdown()


def test_disabled_client_refuses_catalog_calls():
    w = Whisperr(api_key="", disabled=True)
    with pytest.raises(WhisperrConfigurationError):
        w.catalog.heartbeat(1)


def test_main_client_catalog_is_cached_and_uses_its_key_and_base(monkeypatch):
    w = Whisperr(api_key="wrk_live_k", base_url="https://eu.api.test/", flush_interval=60)
    try:
        assert w.catalog is w.catalog
        http = Scripted([(200, {"refreshed": True})])
        monkeypatch.setattr(w.catalog, "_http", http)
        assert w.catalog.heartbeat(3) == {"refreshed": True}
        assert http.calls[0]["url"] == "https://eu.api.test/v1/catalog/heartbeat"
        assert http.calls[0]["headers"]["X-API-Key"] == "wrk_live_k"
        assert http.calls[0]["headers"]["User-Agent"].startswith("whisperr-python/")
    finally:
        w.shutdown()


def test_retries_500_and_network_errors_with_backoff_and_the_same_body():
    waits = []
    http = Scripted([(500, None), OSError("connection reset"), ok(1)])
    c = client(http, sleep=waits.append)
    assert c.upsert([item("a")])["accepted"] == 1
    assert len(http.calls) == 3
    assert len({x["raw"] for x in http.calls}) == 1
    assert waits[0] >= 1.0 and waits[1] >= 2.0


def test_exhausted_retries_raise_retryable_with_partial():
    http = Scripted([ok(500), (503, None), (503, None), (503, None)])
    c = client(http, max_retries=2)
    with pytest.raises(WhisperrCatalogError) as e:
        c.upsert([item(f"s{i}") for i in range(501)])
    assert e.value.retryable is True
    assert e.value.status == 503
    assert e.value.partial["accepted"] == 500
    assert len(http.calls) == 4


def test_4xx_is_not_retried():
    http = Scripted([(400, {"error": {"code": "invalid_request", "message": "bad"}})])
    with pytest.raises(WhisperrCatalogError) as e:
        client(http).upsert([item("a")])
    assert (e.value.status, e.value.code, e.value.retryable) == (400, "invalid_request", False)
    assert len(http.calls) == 1


def test_413_splits_and_rejects_a_lone_item_still_too_large():
    def reply(body):
        if len(body["items"]) > 1 or body["items"][0]["source_item_id"] == "big":
            return (413, {"error": {"code": "batch_too_large", "message": "too large"}})
        return ok(1)

    http = Scripted(reply)
    res = client(http).upsert([item("a"), item("big"), item("c")])
    assert res["accepted"] == 2
    assert [(r["index"], r["source_item_id"], r["code"]) for r in res["rejected"]] == [(1, "big", "item_too_large")]


def test_chunks_by_body_size():
    http = Scripted(lambda body: ok(len(body["items"])))
    items = [item(f"s{i}", tags=["x" * 200] * 60) for i in range(400)]
    assert client(http).upsert(items)["accepted"] == 400
    assert len(http.calls) > 1
    assert all(len(x["raw"]) <= MAX_BODY_BYTES for x in http.calls)


def test_datetimes_and_none_values():
    http = Scripted([ok(1)])
    tz = timezone(timedelta(hours=2))
    client(http).upsert([item(
        "a",
        updated_at=datetime(2026, 10, 23, 9, 0, 0, 120000, tzinfo=tz),
        published_at=datetime(2026, 9, 1),
        url=None,
        price={"amount_minor": to_minor_units("19.99", "USD"), "currency": "USD", "meaning": None},
    )])
    sent = http.calls[0]["body"]["items"][0]
    assert sent["updated_at"] == "2026-10-23T07:00:00.120Z"
    assert sent["published_at"] == "2026-09-01T00:00:00Z"
    assert "url" not in sent
    assert sent["price"] == {"amount_minor": 1999, "currency": "USD"}


def test_delete_uses_one_deleted_at_across_chunks():
    http = Scripted(lambda body: (200, {"deleted": len(body["items"]), "ignored_older": 0, "not_found": 0, "rejected": []}))
    ids = [f"s{i}" for i in range(1500)]
    res = client(http).delete(ids + [{"source_item_id": "own", "deleted_at": "2026-01-01T00:00:00Z"}])
    assert res["deleted"] == 1501
    stamps = {i["deleted_at"] for x in http.calls for i in x["body"]["items"] if i["source_item_id"] != "own"}
    assert len(stamps) == 1
    assert http.calls[1]["body"]["items"][-1] == {"source_item_id": "own", "deleted_at": "2026-01-01T00:00:00Z"}


def test_counts_are_checked_locally():
    c = client(Scripted([]))
    with pytest.raises(WhisperrConfigurationError):
        c.heartbeat(-1)
    with pytest.raises(WhisperrConfigurationError):
        c.complete_snapshot("snap_1", True)
    with pytest.raises(WhisperrConfigurationError):
        c.upsert({"source_item_id": "a"})


def test_parse_retry_after():
    now = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    assert parse_retry_after("7", now) == 7.0
    assert parse_retry_after("3600", now) == 60.0
    assert parse_retry_after("Sat, 26 Sep 2026 12:00:05 GMT", now) == 5.0
    assert parse_retry_after("soon", now) is None
    assert parse_retry_after(None, now) is None


def test_minor_units_from_numbers():
    assert to_minor_units(19.99, "USD") == 1999
    assert to_minor_units(1500, "JPY") == 1500
    assert to_minor_units(Decimal("1.234"), "kwd") == 1234
    for bad in (0.1 + 0.2, 1e-7, float("nan"), 1e300, True, None):
        with pytest.raises(ValueError):
            to_minor_units(bad, "USD")
