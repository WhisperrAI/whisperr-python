"""Event names the Whisperr spec reserves for membership billing (the
playbook engine spec, section 3.8).

Send them from your backend with :meth:`whisperr.Whisperr.track` when Whisperr
has no billing source connected (for example a store webhook handler). Never
send them from the front end. Today the server stores these events only after
the app registers them.

Money properties, where they apply: ``plan_id``, ``price`` (number, major
units, for example ``9.99``), ``currency`` (ISO 4217, upper case),
``plan_period`` (ISO 8601, for example ``P1M``), ``transaction_id``, ``store``.
"""

from __future__ import annotations


class BillingEvent:
    """Reserved billing fallback event names."""

    TRIAL_STARTED = "trial_started"  # A free trial starts.
    TRIAL_CONVERTED = "trial_converted"  # The trial converts to the first paid period.
    TRIAL_AUTO_RENEW_DISABLED = "trial_auto_renew_disabled"  # The user cancels during the trial.
    MEMBERSHIP_STARTED = "membership_started"  # A paid membership starts (never a renewal or a plan change).
    RENEWAL_SUCCEEDED = "renewal_succeeded"  # A paid period renews.
    AUTO_RENEW_DISABLED = "auto_renew_disabled"  # The user cancels; access continues to the period end.
    AUTO_RENEW_ENABLED = "auto_renew_enabled"  # The user turns auto-renew back on.
    MEMBERSHIP_ENDED = "membership_ended"  # Access ends.
    BILLING_ISSUE_STARTED = "billing_issue_started"  # A payment fails and a billing issue starts.
    BILLING_RECOVERED = "billing_recovered"  # The billing issue is resolved.
    REFUND_GRANTED = "refund_granted"  # The store grants a refund.
