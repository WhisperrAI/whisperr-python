"""Catalog push conformance (fixtures/catalog_push.json).

whisperr-spec conformance/catalog_push.json wins when WHISPERR_CATALOG_SPEC_PATH
points at it; until the spec publishes it, the bundled copy is the contract.
Python items and results use the wire names, so fixture inputs are passed as is.
"""

import json
import os
from urllib.parse import urlsplit

import pytest

from whisperr import CatalogClient, WhisperrCatalogError, WhisperrConfigurationError, to_minor_units

HERE = os.path.dirname(__file__)
PATH = os.environ.get("WHISPERR_CATALOG_SPEC_PATH") or os.path.join(HERE, "fixtures", "catalog_push.json")
with open(PATH) as f:
    SPEC = json.load(f)


def _run(case, sent):
    exchanges = iter(case["exchanges"])

    def http(method, url, headers, body, timeout):
        x = next(exchanges, None)
        if x is None:
            raise AssertionError(f"{case['name']}: unexpected request {method} {url}")
        sent.append({"method": method, "url": url, "headers": headers, "raw": body,
                     "body": None if body is None else json.loads(body)})
        r = x["response"]
        raw = b"" if "body" not in r else json.dumps(r["body"]).encode()
        return r["status"], r.get("headers", {}), raw

    client = CatalogClient(case.get("api_key", "wrk_test"), base_url="https://api.test", http=http, sleep=lambda s: None)
    a = case["args"]
    op = case["op"]
    if op == "upsert":
        items = a.get("items")
        if items is None:
            g = a["generate_items"]
            items = [dict(g["template"], source_item_id=f"{g['source_item_id_prefix']}{i}") for i in range(g["count"])]
        return client.upsert(items)
    if op == "delete":
        if "items" in a:
            return client.delete(a["items"])
        ids = a.get("ids") or [f"{a['generate_ids']['prefix']}{i}" for i in range(a["generate_ids"]["count"])]
        return client.delete(ids, a.get("deleted_at"))
    if op == "begin_snapshot":
        return client.begin_snapshot()
    if op == "complete_snapshot":
        return client.complete_snapshot(a["snapshot_id"], a["item_count"])
    if op == "heartbeat":
        return client.heartbeat(a["item_count"])
    if op == "status":
        return client.status()
    raise AssertionError(f"unknown op {op}")


@pytest.mark.parametrize("case", SPEC["cases"], ids=[c["name"] for c in SPEC["cases"]])
def test_catalog_case(case):
    sent = []
    result = error = None
    try:
        result = _run(case, sent)
    except Exception as e:  # noqa: BLE001 - asserted below
        error = e
    assert len(sent) == len(case["exchanges"]), error
    for i, x in enumerate(case["exchanges"]):
        req, got = x["request"], sent[i]
        assert got["method"] == req["method"]
        assert urlsplit(got["url"]).path == req["path"]
        assert got["headers"]["X-API-Key"] == case.get("api_key", "wrk_test")
        if req["method"] == "GET":
            assert got["raw"] is None
        else:
            assert got["headers"]["Content-Type"] == "application/json"
        if "body" in req:
            assert got["body"] == req["body"]
        if "item_ids" in req:
            assert [it["source_item_id"] for it in got["body"]["items"]] == req["item_ids"]
        if "item_count" in req:
            items = got["body"]["items"]
            assert len(items) == req["item_count"]
            assert items[0]["source_item_id"] == req["first_item_id"]
            assert items[-1]["source_item_id"] == req["last_item_id"]
        if req.get("same_body_as_previous"):
            assert got["raw"] == sent[i - 1]["raw"]
    expected_error = case.get("error")
    if expected_error:
        if expected_error.get("configuration") or expected_error.get("local"):
            assert isinstance(error, WhisperrConfigurationError)
        else:
            assert isinstance(error, WhisperrCatalogError), error
            assert error.status == expected_error["status"]
            assert error.code == expected_error["code"]
            assert error.retryable == expected_error["retryable"]
    else:
        if error:
            raise error
        assert result == case["result"]


@pytest.mark.parametrize("m", SPEC["minor_units"], ids=[f"{m['amount']} {m['currency']}" for m in SPEC["minor_units"]])
def test_minor_units(m):
    if "error" in m:
        with pytest.raises(ValueError):
            to_minor_units(m["amount"], m["currency"])
    else:
        assert to_minor_units(m["amount"], m["currency"]) == m["minor"]
