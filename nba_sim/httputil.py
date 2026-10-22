"""Shared HTTP helpers for NBA Stats / retries.

nba_api calls often need a browser-like User-Agent; we set headers once and
reuse a pause+retry wrapper everywhere (data + schedule).
"""

from __future__ import annotations

import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")

API_TIMEOUT = 120
API_RETRIES = 4
REQUEST_PAUSE = 0.75

# Headers that improve success rate against stats.nba.com CDN quirks.
NBA_STATS_HEADERS: dict[str, str] = {
    "Host": "stats.nba.com",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Connection": "keep-alive",
    "Referer": "https://www.nba.com/",
}


def pause(seconds: float | None = None) -> None:
    time.sleep(REQUEST_PAUSE if seconds is None else seconds)


def with_retries(label: str, fn: Callable[[], T], *, retries: int = API_RETRIES) -> T:
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            pause()
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            wait = min(2**attempt, 20)
            print(f"  retry {attempt}/{retries} {label}: {exc} (sleep {wait}s)")
            time.sleep(wait)
    raise RuntimeError(f"{label} failed after {retries} tries: {last}")


def endpoint_kwargs(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Common kwargs for nba_api stats endpoints."""
    kw: dict[str, Any] = {
        "timeout": API_TIMEOUT,
        "headers": dict(NBA_STATS_HEADERS),
    }
    if extra:
        kw.update(extra)
    return kw
