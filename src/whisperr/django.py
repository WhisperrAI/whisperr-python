"""Django integration for Whisperr.

Add to settings.py::

    WHISPERR_API_KEY = os.environ["WHISPERR_API_KEY"]
    MIDDLEWARE = [
        # ... after your authentication middleware ...
        "whisperr.django.WhisperrMiddleware",
    ]

Then in a view::

    request.whisperr.track("plan_upgraded", {"plan": "pro"})

For events with no request (Celery tasks, webhooks, management commands), use a
client directly with the user id from your domain data.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Callable, Dict, Optional, Sequence

from .client import Whisperr

_client: Optional[Whisperr] = None


def get_client() -> Whisperr:
    """The process-wide Whisperr client, built from Django settings on first use."""
    global _client
    if _client is None:
        from django.conf import settings

        _client = Whisperr(
            api_key=getattr(settings, "WHISPERR_API_KEY", ""),
            base_url=getattr(settings, "WHISPERR_BASE_URL", "https://api.whisperr.net"),
            disabled=getattr(settings, "WHISPERR_DISABLED", False),
            debug=getattr(settings, "WHISPERR_DEBUG", False),
        )
    return _client


class _RequestWhisperr:
    """Per-request helper bound to the request's resolved user."""

    def __init__(self, client: Whisperr, user_id: Optional[str]) -> None:
        self.client = client
        self.user_id = user_id

    def track(
        self,
        event_type: str,
        properties: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.user_id:
            return
        self.client.track(self.user_id, event_type, properties, context)

    def identify(self, **kwargs: Any) -> None:
        if not self.user_id:
            return
        self.client.identify(self.user_id, **kwargs)


def _default_resolve_user(request: Any) -> Optional[str]:
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return str(user.pk)
    return None


def _import_callable(dotted: str) -> Callable[[Any], Optional[str]]:
    module_path, _, attr = dotted.rpartition(".")
    module = import_module(module_path)
    return getattr(module, attr)


class WhisperrMiddleware:
    """Attaches ``request.whisperr`` bound to the request's user."""

    def __init__(self, get_response: Callable[[Any], Any]) -> None:
        self.get_response = get_response
        self.client = get_client()
        from django.conf import settings

        resolver = getattr(settings, "WHISPERR_RESOLVE_USER", None)
        self.resolve_user = _import_callable(resolver) if resolver else _default_resolve_user

    def __call__(self, request: Any) -> Any:
        request.whisperr = _RequestWhisperr(self.client, self.resolve_user(request))
        return self.get_response(request)
