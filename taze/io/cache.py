"""Read and write the local registry response cache."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import msgspec


TTL = 30 * 60


class RegistryCache(dict[str, Any]):
    """A ``{key: response}`` mapping that remembers when each entry was stored.

    Registry clients treat it as a plain dict. On save every entry is written
    with its own timestamp, so packages expire individually instead of the
    whole file being invalidated (or kept alive) by its modification time.
    """

    def __init__(self, entries: dict[str, Any] | None = None, fetched_at: dict[str, float] | None = None) -> None:
        super().__init__(entries or {})
        self.fetched_at: dict[str, float] = dict(fetched_at or {})

    def __setitem__(self, key: str, value: Any) -> None:
        super().__setitem__(key, value)
        self.fetched_at[key] = time.time()


def cache_path() -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return root / "taze" / "pypi.json"


def load_cache(*, force: bool = False, ttl: float = TTL) -> RegistryCache:
    """Load unexpired entries. ``force`` starts from an empty cache but still persists new lookups."""
    if force:
        return RegistryCache()
    path = cache_path()
    try:
        file_time = path.stat().st_mtime
        data = msgspec.json.decode(path.read_bytes())
    except OSError, msgspec.DecodeError:
        return RegistryCache()
    if not isinstance(data, dict):
        return RegistryCache()

    now = time.time()
    entries: dict[str, Any] = {}
    fetched_at: dict[str, float] = {}
    for key, entry in data.items():
        if not isinstance(key, str):
            continue
        # Entries written before per-entry timestamps existed carry no
        # ``fetched_at``; the file's mtime is the best estimate for those.
        if isinstance(entry, dict) and "data" in entry and isinstance(entry.get("fetched_at"), int | float):
            stamp, value = float(entry["fetched_at"]), entry["data"]
        else:
            stamp, value = file_time, entry
        if now - stamp >= ttl:
            continue
        entries[key] = value
        fetched_at[key] = stamp
    return RegistryCache(entries, fetched_at)


def save_cache(cache: dict[str, Any]) -> None:
    """Persist the cache atomically so a concurrent ``taze`` run never reads a half-written file."""
    if not cache:
        return
    now = time.time()
    stamps = cache.fetched_at if isinstance(cache, RegistryCache) else {}
    payload = {key: {"fetched_at": stamps.get(key, now), "data": value} for key, value in cache.items()}
    path = cache_path()
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(msgspec.json.encode(payload))
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
