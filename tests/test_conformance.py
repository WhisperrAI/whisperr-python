import json
import os
import re
import urllib.request

from whisperr import Whisperr

SPEC_URL = "https://raw.githubusercontent.com/WhisperrAI/whisperr-spec/main/conformance/wire.json"
RFC3339_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def load_spec():
    local = os.environ.get("WHISPERR_SPEC_PATH")
    if local:
        with open(local) as f:
            return json.load(f)
    with urllib.request.urlopen(SPEC_URL, timeout=15) as r:
        return json.loads(r.read())


def apply_case(client, case):
    s = case["scenario"]
    if case["op"] == "track":
        client.track(s["externalUserId"], s["eventType"], s.get("properties"))
        return
    channels = None
    if s.get("channels"):
        channels = []
        for ch in s["channels"]:
            c = {"type": ch["type"], "address": ch["address"], "opted_in": ch.get("optedIn", True)}
            if "verified" in ch:
                c["verified"] = ch["verified"]
            channels.append(c)
    client.identify(
        s["externalUserId"],
        traits=s.get("traits"),
        email=s.get("email"),
        phone=s.get("phone"),
        push_token=s.get("pushToken"),
        preferred_channel=s.get("preferredChannel"),
        channels=channels,
    )


def test_wire_conformance(monkeypatch):
    # Load the spec first, before we patch urlopen to capture SDK requests.
    spec = load_spec()
    assert spec["cases"], "spec has no cases"

    captured = []

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured.append({"url": req.full_url, "body": json.loads(req.data.decode())})
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = Whisperr(api_key="wrk_test", flush_interval=0.05)

    for case in spec["cases"]:
        captured.clear()
        apply_case(client, case)
        client.flush()

        call = next((c for c in captured if c["url"].endswith(case["endpoint"])), None)
        assert call is not None, f"{case['name']}: expected POST {case['endpoint']}"

        if case["op"] == "track":
            event = call["body"]["events"][0]
            for k, v in case.get("expectedEvent", {}).items():
                assert event[k] == v, f"{case['name']}.{k}"
            for key in case.get("contextMustContain", []):
                assert key in event["context"], f"{case['name']} context.{key}"
            if case.get("occurredAtRfc3339Z"):
                assert RFC3339_Z.match(event["occurred_at"]), event["occurred_at"]
        else:
            for k, v in case.get("expectedBody", {}).items():
                assert call["body"][k] == v, f"{case['name']}.{k}"

    client.shutdown()
