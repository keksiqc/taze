"""Read and write the local registry response cache."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


TTL = 30 * 60


def cache_path() -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return root / "taze" / "pypi.json"


def load_cache(*, force: bool = False) -> dict[str, dict]:
    if force:
        return {}
    path = cache_path()
    try:
        if time.time() - path.stat().st_mtime >= TTL:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except OSError, ValueError:
        return {}


def save_cache(cache: dict[str, dict]) -> None:
    if not cache:
        return
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass
