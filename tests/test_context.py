from importlib import metadata

import whisperr
from whisperr import SDK_NAME, BillingEvent, Whisperr


class CapturingTransport:
    def __init__(self):
        self.events = []

    def send_batch(self, events):
        self.events.extend(dict(e) for e in events)
        return "ok"

    def send_identify(self, op):
        return "ok"


def send_one(context=None, **kwargs):
    t = CapturingTransport()
    w = Whisperr(api_key="wrk_test", transport=t, flush_interval=60, **kwargs)
    try:
        w.track("user_1", "feature_used", {}, context)
        w.flush()
    finally:
        w.shutdown()
    return t.events[0]["context"]


def test_sends_sdk_name_and_version_and_no_app_version_by_default():
    ctx = send_one()
    assert ctx["sdk_name"] == "whisperr-python" == SDK_NAME
    assert ctx["sdk_version"] == whisperr.__version__
    assert "app_version" not in ctx


def test_sends_app_version_when_configured():
    assert send_one(app_version="2026.10.1")["app_version"] == "2026.10.1"


def test_blank_app_version_sends_no_key():
    assert "app_version" not in send_one(app_version="  ")


def test_caller_context_key_wins():
    ctx = send_one({"app_version": "relay-9", "plan": "pro"}, app_version="1.0.0")
    assert ctx["app_version"] == "relay-9"
    assert ctx["plan"] == "pro"
    assert ctx["sdk_name"] == SDK_NAME


def test_caller_context_is_not_mutated():
    caller = {"plan": "pro"}
    send_one(caller)
    assert caller == {"plan": "pro"}


def test_sdk_version_matches_the_package_metadata():
    # pyproject reads the version from _version.py; this catches a broken link.
    assert metadata.version("whisperr") == whisperr.__version__


def test_billing_event_names():
    names = sorted(v for k, v in vars(BillingEvent).items() if k.isupper())
    assert names == [
        "auto_renew_disabled",
        "auto_renew_enabled",
        "billing_issue_started",
        "billing_recovered",
        "membership_ended",
        "membership_started",
        "refund_granted",
        "renewal_succeeded",
        "trial_auto_renew_disabled",
        "trial_converted",
        "trial_started",
    ]
