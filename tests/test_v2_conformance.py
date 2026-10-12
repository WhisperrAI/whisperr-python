"""Contract v2 flows (whisperr-spec conformance/v2.json).

This harness runs every section whose appliesTo names "server".
"""

import json
import os
import urllib.request
from pathlib import Path

import whisperr
from whisperr import SDK_NAME, Whisperr

SPEC_URL = "https://raw.githubusercontent.com/WhisperrAI/whisperr-spec/main/conformance/v2.json"
GOVERNED_CONTEXT_KEYS = ("app_version", "app_build", "platform", "sdk_name", "sdk_version", "locale")
PLACEHOLDERS = {"$sdk_name": SDK_NAME, "$sdk_version": whisperr.__version__}


def load_spec():
    local = os.environ.get("WHISPERR_V2_SPEC_PATH") or sibling_path()
    if local:
        with open(local) as f:
            return json.load(f)
    with urllib.request.urlopen(SPEC_URL, timeout=15) as r:
        return json.loads(r.read())


def sibling_path():
    wire = os.environ.get("WHISPERR_SPEC_PATH")
    return str(Path(wire).with_name("v2.json")) if wire else None


def resolve(value):
    if isinstance(value, str):
        return PLACEHOLDERS.get(value, value)
    if isinstance(value, list):
        return [resolve(v) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v) for k, v in value.items()}
    return value


class CapturingTransport:
    """Serializes through the real Transport and records each request."""

    def __init__(self):
        from whisperr.transport import Transport

        self.requests = []
        transport = Transport("https://api.test", "wrk_test", 1.0, lambda _msg: None)
        transport._post = lambda path, body: self.requests.append((path, body)) or "ok"
        self._real = transport

    def send_batch(self, events):
        return self._real.send_batch(events)

    def send_identify(self, op):
        return self._real.send_identify(op)


def apply_step(client, step, label):
    if "track" in step:
        t = step["track"]
        client.track(t["externalUserId"], t["eventType"], t.get("properties"))
    elif "identify" in step:
        s = step["identify"]
        client.identify(s["externalUserId"], traits=s.get("traits"), push_token=s.get("pushToken"))
    else:
        raise AssertionError(f"{label}: step {step!r} is not supported by a server SDK")


def assert_event(actual, expected, label):
    expected = resolve(expected)
    expected_context = expected.pop("context", None)
    actual = dict(actual)
    actual.pop("occurred_at", None)
    actual_context = actual.pop("context", {})
    assert actual == expected, label
    assert isinstance(actual_context.get("$message_id"), str), f"{label} context.$message_id"
    if expected_context is None:
        return
    for key in GOVERNED_CONTEXT_KEYS:
        if key in expected_context:
            assert actual_context.get(key) == expected_context[key], f"{label} context.{key}"
        else:
            assert key not in actual_context, f"{label} context.{key} must be absent"


def test_v2_server_sections():
    spec = load_spec()
    sections = {n: s for n, s in spec["sections"].items() if "server" in s["appliesTo"]}
    assert sections, "v2.json has no server section"

    for section_name, section in sections.items():
        for case in section["cases"]:
            label = f"{section_name}/{case['name']}"
            transport = CapturingTransport()
            client = Whisperr(
                api_key="wrk_test",
                transport=transport,
                flush_interval=60,
                app_version=(case.get("config") or {}).get("release"),
            )
            try:
                for step in case["steps"]:
                    apply_step(client, step, label)
                    client.flush()
            finally:
                client.shutdown()

            got = transport.requests
            want = case["expectedRequests"]
            assert len(got) == len(want), f"{label}: request count {len(got)} != {len(want)}"
            for i, (expected, (path, body)) in enumerate(zip(want, got)):
                assert path == expected["endpoint"], f"{label}[{i}] endpoint"
                if expected["endpoint"] == "/v1/identify":
                    assert body == resolve(expected["body"]), f"{label}[{i}] body"
                else:
                    events = body.get("events", [body])
                    assert len(events) == len(expected["events"]), f"{label}[{i}] event count"
                    for j, (a, e) in enumerate(zip(events, expected["events"])):
                        assert_event(a, e, f"{label}[{i}].events[{j}]")
