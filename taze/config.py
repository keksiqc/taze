"""Loading and validating project-local taze configuration."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


CONFIG_KEYS = {
    "include",
    "exclude",
    "recursive",
    "ignore_paths",
    "ignore_other_workspaces",
    "include_locked",
    "concurrency",
}


def load_config(root: Path, config_path: Path | None = None) -> dict[str, Any]:
    """Load ``taze.toml`` or ``[tool.taze]`` and return supported options."""
    path = config_path or root / "taze.toml"
    if path.is_file():
        with path.open("rb") as f:
            data = tomllib.load(f)
        data = data.get("taze", data)
    else:
        pyproject = root / "pyproject.toml"
        if not pyproject.is_file():
            return {}
        with pyproject.open("rb") as f:
            data = tomllib.load(f).get("tool", {}).get("taze", {})
    if not isinstance(data, dict):
        return {}
    config: dict[str, Any] = {}
    for key, value in data.items():
        normalized = key.replace("-", "_")
        if normalized in CONFIG_KEYS:
            config[normalized] = value
    return config
