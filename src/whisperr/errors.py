from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class WhisperrError:
    """Passed to the ``on_error`` callback for delivery/drop observability."""

    type: str  # "auth" | "dropped" | "retry_exhausted"
    message: str
    status: Optional[int] = None


class WhisperrConfigurationError(ValueError):
    """A key or argument the catalog API can never accept. Nothing was sent."""


class WhisperrCatalogError(Exception):
    """Whisperr refused a catalog call, or could not be reached.

    ``status`` is the HTTP status (0 for a network error or timeout), ``code``
    the server's error code (``server_key_required``, ``catalog_push_disabled``,
    ``snapshot_not_open``, ...) or ``network_error``. ``retryable`` is true when
    retries ran out on a transient failure. For upsert and delete, ``partial``
    holds what earlier chunks already did.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int,
        code: str,
        retryable: bool,
        partial: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.retryable = retryable
        self.partial = partial
