from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class WhisperrError:
    """Passed to the ``on_error`` callback for delivery/drop observability."""

    type: str  # "auth" | "dropped" | "retry_exhausted"
    message: str
    status: Optional[int] = None
