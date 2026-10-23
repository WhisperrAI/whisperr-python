"""Catalog push conformance (fixtures/catalog_push.json).

tests/fixtures/catalog_push.json is a byte-identical copy of whisperr-spec
conformance/catalog_push.json. Point WHISPERR_CATALOG_SPEC_PATH at a spec
checkout's file to run that copy instead and to check the bundled one has not
drifted from it. Python items and results use the wire names, so fixture
inputs are passed as is.
"""

import json
import os
from urllib.parse import urlsplit

import pytest

from whisperr import CatalogClient, WhisperrCatalogError, WhisperrConfigurationError, to_minor_units

HERE = os.path.dirname(__file__)
BUNDLED = os.path.join(HERE, "fixtures", "catalog_push.json")
SPEC_PATH = os.environ.get("WHISPERR_CATALOG_SPEC_PATH")
PATH = SPEC_PATH or BUNDLED
with open(PATH) as f:
    SPEC = json.load(f)
# Client-side reject codes carry an SDK-defined message.
CLIENT_CODES = set(SPEC["reject_codes"]["client"])


def _expected_result(case, result):
    want = json.loads(json.dumps(case["result"]))
    if isinstance(want.get("rejected"), list) and isinstance(result, dict):
        for i, r in enumerate(want["rejected"]):
            if r["code"] in CLIENT_CODES:
                got = result.get("rejected", [])[i] if i < len(result.get("rejected", [])) else {}
                message = got.get("message")
                assert isinstance(message, str) and message.strip(), "a client-side reject needs a message"
                r["message"] = message
    return want


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
        assert result == _expected_result(case, result)


@pytest.mark.parametrize("m", SPEC["minor_units"], ids=[f"{m['amount']} {m['currency']}" for m in SPEC["minor_units"]])
def test_minor_units(m):
    if "error" in m:
        with pytest.raises(ValueError):
            to_minor_units(m["amount"], m["currency"])
    else:
        assert to_minor_units(m["amount"], m["currency"]) == m["minor"]


@pytest.mark.skipif(not SPEC_PATH, reason="WHISPERR_CATALOG_SPEC_PATH is not set")
def test_bundled_fixture_is_byte_identical_to_the_spec_copy():
    with open(BUNDLED, "rb") as a, open(SPEC_PATH, "rb") as b:
        assert a.read() == b.read()


def test_the_only_sdk_written_reject_code_is_listed_as_client_side():
    # item_too_large is the only code the SDK adds; the server never sends it.
    assert SPEC["reject_codes"]["client"] == ["item_too_large"]
    assert "item_too_large" not in SPEC["reject_codes"]["server"]
