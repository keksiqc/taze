"""Loading and validating project-local taze configuration."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from taze.models import MODES


CONFIG_KEYS = {
    "include",
    "exclude",
    "recursive",
    "ignore_paths",
    "ignore_other_workspaces",
    "include_locked",
    "concurrency",
    "maturity_period",
    "maturity_period_exclude",
    "package_mode",
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


def package_mode_for(name: str, package_modes: object) -> str | None:
    """Return an exact or slash-delimited-regex policy for a package name."""
    if not isinstance(package_modes, dict):
        return None
    for pattern, mode in package_modes.items():
        if not isinstance(pattern, str) or not isinstance(mode, str):
            continue
        if mode != "ignore" and mode not in MODES:
            continue
        if pattern == name:
            return mode
        if pattern.startswith("/") and pattern.endswith("/"):
            import re

            if re.fullmatch(pattern[1:-1], name):
                return mode
    return None
