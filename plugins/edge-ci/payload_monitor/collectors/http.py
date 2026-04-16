"""Shared HTTP session with automatic retry for transient failures."""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class TimeoutSession(requests.Session):
    """Session subclass that enforces a default timeout on all requests."""

    def __init__(self, default_timeout: float = 30.0):
        super().__init__()
        self._default_timeout = default_timeout

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", self._default_timeout)
        return super().request(method, url, **kwargs)


def create_session(
    retries: int = 3,
    backoff_factor: float = 0.5,
    status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504),
    default_timeout: float = 30.0,
) -> requests.Session:
    """Create a requests Session with retry/backoff for transient errors."""
    session = TimeoutSession(default_timeout=default_timeout)
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
