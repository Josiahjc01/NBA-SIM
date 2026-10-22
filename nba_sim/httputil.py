"""Shared HTTP helpers for NBA Stats / ESPN.

Tunables via env (all optional):
  NBA_SIM_TIMEOUT     — seconds per request (default 30)
  NBA_SIM_RETRIES     — hard retries for required feeds (default 3)
  NBA_SIM_PAUSE       — pause before each live call (default 0.55)
  NBA_SIM_CACHE_TTL   — endpoint cache TTL seconds (default 21600 = 6h)
  NBA_SIM_MAX_WORKERS — parallel fetch workers (default 4)

Defaults keep a single bad endpoint from stalling a live run for many
minutes (previously 90s × 5 retries). Raise via env when the CDN is slow.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")

API_TIMEOUT = int(os.environ.get("NBA_SIM_TIMEOUT", "30"))
API_RETRIES = int(os.environ.get("NBA_SIM_RETRIES", "3"))
REQUEST_PAUSE = float(os.environ.get("NBA_SIM_PAUSE", "0.55"))
ENDPOINT_CACHE_TTL = int(os.environ.get("NBA_SIM_CACHE_TTL", str(6 * 60 * 60)))
MAX_WORKERS = max(1, int(os.environ.get("NBA_SIM_MAX_WORKERS", "4")))

CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "endpoints"

# Browser-like headers — stats.nba.com CDN is picky about UA / origin.
NBA_STATS_HEADERS: dict[str, str] = {
    "Host": "stats.nba.com",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Origin": "https://www.nba.com",
    "Connection": "keep-alive",
    "Referer": "https://www.nba.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}


def pause(seconds: float | None = None) -> None:
    base = REQUEST_PAUSE if seconds is None else seconds
    # Small jitter so parallel workers don't stampede the CDN.
    time.sleep(max(0.0, base + random.uniform(0.0, 0.35)))


def _endpoint_cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return CACHE_DIR / f"{digest}.json"


def read_endpoint_cache(key: str, *, use_cache: bool = True) -> Any | None:
    if not use_cache:
        return None
    path = _endpoint_cache_path(key)
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > ENDPOINT_CACHE_TTL:
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write_endpoint_cache(key: str, payload: Any) -> None:
    path = _endpoint_cache_path(key)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
    except OSError:
        pass


def with_retries(
    label: str,
    fn: Callable[[], T],
    *,
    retries: int | None = None,
    required: bool = True,
    use_cache: bool = False,
    cache_key: str | None = None,
    quiet: bool = False,
) -> T:
    """
    Call `fn` with pause + exponential backoff (+ jitter).

    If `use_cache` and `cache_key` are set, successful JSON-serializable results
    are cached under `.cache/endpoints/` and reused within TTL.
    """
    n = API_RETRIES if retries is None else max(1, retries)
    if use_cache and cache_key:
        hit = read_endpoint_cache(cache_key, use_cache=True)
        if hit is not None:
            if not quiet:
                print(f"  cache hit: {label}")
            return hit  # type: ignore[return-value]

    last: Exception | None = None
    for attempt in range(1, n + 1):
        try:
            pause()
            result = fn()
            if use_cache and cache_key is not None:
                try:
                    write_endpoint_cache(cache_key, result)
                except TypeError:
                    pass  # non-JSON result
            return result
        except Exception as exc:  # noqa: BLE001
            last = exc
            wait = min(2**attempt, 24) + random.uniform(0.0, 1.5)
            if not quiet:
                print(f"  retry {attempt}/{n} {label}: {exc} (sleep {wait:.1f}s)")
            time.sleep(wait)
    msg = f"{label} failed after {n} tries: {last}"
    if required:
        raise RuntimeError(msg)
    raise RuntimeError(msg)  # caller may catch for optional feeds


def soft_fetch(
    label: str,
    fn: Callable[[], T],
    *,
    retries: int = 2,
    use_cache: bool = True,
    cache_key: str | None = None,
    default: T | None = None,
) -> tuple[T | None, str | None]:
    """
    Optional feed helper. Returns (result, error_message).
    Never raises — failed optional feeds become loud provenance.
    """
    try:
        val = with_retries(
            label,
            fn,
            retries=retries,
            required=False,
            use_cache=use_cache,
            cache_key=cache_key,
        )
        return val, None
    except Exception as exc:  # noqa: BLE001
        return default, f"{label}: {exc}"


def parallel_map(
    jobs: list[tuple[str, Callable[[], Any]]],
    *,
    max_workers: int | None = None,
) -> dict[str, Any]:
    """
    Run independent callables concurrently. Returns {label: result_or_exception}.
    """
    workers = max_workers or MAX_WORKERS
    out: dict[str, Any] = {}
    if not jobs:
        return out
    if workers <= 1 or len(jobs) == 1:
        for label, fn in jobs:
            try:
                out[label] = fn()
            except Exception as exc:  # noqa: BLE001
                out[label] = exc
        return out

    with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        fut_map = {pool.submit(fn): label for label, fn in jobs}
        for fut in as_completed(fut_map):
            label = fut_map[fut]
            try:
                out[label] = fut.result()
            except Exception as exc:  # noqa: BLE001
                out[label] = exc
    return out


def endpoint_kwargs(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Common kwargs for nba_api stats endpoints."""
    kw: dict[str, Any] = {
        "timeout": API_TIMEOUT,
        "headers": dict(NBA_STATS_HEADERS),
    }
    if extra:
        kw.update(extra)
    return kw


def env_status() -> dict[str, Any]:
    return {
        "NBA_SIM_TIMEOUT": API_TIMEOUT,
        "NBA_SIM_RETRIES": API_RETRIES,
        "NBA_SIM_PAUSE": REQUEST_PAUSE,
        "NBA_SIM_CACHE_TTL": ENDPOINT_CACHE_TTL,
        "NBA_SIM_MAX_WORKERS": MAX_WORKERS,
        "endpoint_cache_dir": str(CACHE_DIR),
    }
