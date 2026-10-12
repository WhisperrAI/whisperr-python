"""Whisperr server-side SDK for Python.

Reliable churn-signal event tracking for any Python backend. The backend is
where the highest-signal churn events live (payment failures, cancellations,
trial expiry, usage drops), so this is where Whisperr gets its best signal.

    from whisperr import Whisperr

    whisperr = Whisperr(api_key=os.environ["WHISPERR_API_KEY"])
    whisperr.track("user_8842", "payment_failed", {"amount_cents": 4900})
    whisperr.shutdown()  # flush before the process exits
"""

from ._version import __version__
from .billing import BillingEvent
from .client import SDK_NAME, Whisperr
from .errors import WhisperrError

__all__ = ["BillingEvent", "SDK_NAME", "Whisperr", "WhisperrError", "__version__"]
