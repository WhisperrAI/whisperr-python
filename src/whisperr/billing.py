"""Event names the Whisperr spec reserves for subscription billing.

Send them with :meth:`whisperr.Whisperr.track` when your own backend sees the
billing change (for example a store webhook) and Whisperr has no billing source
connected. Today the server stores these events only after the app registers
them.

Properties the spec defines (all optional): ``product_id``, ``price`` (number,
major units, for example ``9.99``), ``currency`` (ISO 4217, upper case),
``period`` (ISO 8601, for example ``P1M``), ``transaction_id``, ``store``.
"""

from __future__ import annotations


class BillingEvent:
    """Reserved billing fallback event names."""

    TRIAL_STARTED = "trial_started"
    SUBSCRIPTION_STARTED = "subscription_started"
    SUBSCRIPTION_RENEWED = "subscription_renewed"
    PAYMENT_FAILED = "payment_failed"
    SUBSCRIPTION_CANCELLED = "subscription_cancelled"
    PURCHASE_COMPLETED = "purchase_completed"
