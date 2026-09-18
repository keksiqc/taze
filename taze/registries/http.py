"""Retry policy shared by the registry clients."""

from __future__ import annotations

import time
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError


_RETRY_DELAYS = (1.0, 3.0)  # seconds between attempts 1→2 and 2→3
_MAX_RETRY_AFTER = 30.0
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def retry_delay(error: Exception, attempt: int) -> float | None:
    """How long to wait before retrying ``attempt``, or ``None`` if the error is final.

    Connection problems and server-side failures are worth another try.
    Any other HTTP client error (404 for an unknown package, 401/403 for
    a rejected token, and so on) will not change on retry, so we give up
    at once instead of sleeping through the backoff schedule.
    """
    if isinstance(error, HTTPError):
        if error.code not in _RETRYABLE_STATUS:
            return None
        hinted = _retry_after(error.headers.get("Retry-After") if error.headers else None)
        if hinted is not None:
            return min(hinted, _MAX_RETRY_AFTER)
    if attempt < len(_RETRY_DELAYS):
        return _RETRY_DELAYS[attempt]
    return _RETRY_DELAYS[-1] * 2 ** (attempt - len(_RETRY_DELAYS) + 1)


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        delta = parsedate_to_datetime(value).timestamp() - time.time()
    except TypeError, ValueError, OverflowError:
        return None
    return max(0.0, delta)


def is_rate_limited(error: Exception) -> bool:
    """Whether GitHub rejected the request for exhausting the API quota."""
    if not isinstance(error, HTTPError) or error.code not in (403, 429):
        return False
    remaining = error.headers.get("X-RateLimit-Remaining") if error.headers else None
    return error.code == 429 or remaining == "0"
