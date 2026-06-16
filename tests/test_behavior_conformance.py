import json
import os
import urllib.request
from pathlib import Path

from whisperr import Whisperr

SPEC_URL = "https://raw.githubusercontent.com/WhisperrAI/whisperr-spec/main/conformance/behavior.json"


class FakeTransport:
    def __init__(self, result):
        self.result = result
        self.batches = []

    def send_batch(self, events):
        self.batches.append([dict(e) for e in events])
        return self.result

    def send_identify(self, op):
        raise AssertionError("behavior conformance only exercises track delivery")


def load_spec():
    local = os.environ.get("WHISPERR_BEHAVIOR_SPEC_PATH") or sibling_behavior_path()
    if local:
        with open(local) as f:
            return json.load(f)
    with urllib.request.urlopen(SPEC_URL, timeout=15) as r:
        return json.loads(r.read())


def sibling_behavior_path():
    wire = os.environ.get("WHISPERR_SPEC_PATH")
    if not wire:
        return None
    return str(Path(wire).with_name("behavior.json"))


def test_behavior_conformance():
    spec = load_spec()
    assert spec["cases"], "spec has no cases"

    for case in spec["cases"]:
        scenario = case["scenario"]
        errors = []
        transport = FakeTransport(case["firstResponse"]["classification"])
        client = Whisperr(
            api_key="wrk_test",
            transport=transport,
            flush_interval=3600,
            max_retries=case.get("clientOptions", {}).get("maxRetries", 0),
            on_error=errors.append,
        )
        try:
            client.track(
                scenario["externalUserId"],
                scenario["eventType"],
                scenario.get("properties"),
            )
            client.flush()

            assert any(e.type == case["expect"]["errorType"] for e in errors), case["name"]
            attempts_before = len(transport.batches)
            assert attempts_before == 1, f"{case['name']}: first delivery attempt"
            with client._buf_lock:
                retained = len(client._buf) > 0
            assert retained == case["expect"]["retainedAfterFirstFlush"], case["name"]

            transport.result = case["recoveryResponse"]["classification"]
            client.flush()

            attempts_after = len(transport.batches)
            retried = attempts_after > attempts_before
            assert retried == case["expect"]["retriesAfterRecovery"], case["name"]
            delivered = retried and transport.batches[-1][0]["event_type"] == scenario["eventType"]
            assert delivered == case["expect"]["deliveredAfterRecovery"], case["name"]

            if case["expect"].get("stableMessageIdOnRetry"):
                assert transport.batches[1][0]["message_id"] == transport.batches[0][0]["message_id"]
        finally:
            client.shutdown()
