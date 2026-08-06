"""Loading and validating project-local taze configuration."""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path
from typing import Any

from taze.models import MODES


CONFIG_KEYS = {
    "include",
    "exclude",
    "mode",
    "interactive",
    "recursive",
    "ignore_paths",
    "ignore_other_workspaces",
    "include_locked",
    "concurrency",
    "maturity_period",
    "maturity_period_exclude",
    "package_mode",
    "group",
    "all",
    "sort",
    "write",
    "install",
    "update",
    "silent",
    "fail_on_outdated",
    "output_json",
    "force",
    "request_timeout",
    "retry",
    "github_actions",
    "github_actions_style",
}


def load_config(root: Path, config_path: Path | None = None) -> dict[str, Any]:
    """Load ``taze.toml`` or ``[tool.taze]`` and return supported options."""
    path = config_path or root / "taze.toml"
    try:
        if path.is_file():
            with path.open("rb") as f:
                data = tomllib.load(f)
            if isinstance(data.get("tool"), dict) and isinstance(data["tool"].get("taze"), dict):
                data = data["tool"]["taze"]
            else:
                data = data.get("taze", data)
        else:
            pyproject = root / "pyproject.toml"
            if not pyproject.is_file():
                return {}
            with pyproject.open("rb") as f:
                parsed = tomllib.load(f)
            tool = parsed.get("tool", {})
            data = tool.get("taze", {}) if isinstance(tool, dict) else {}
    except OSError, tomllib.TOMLDecodeError:
        return {}

    if not isinstance(data, dict):
        return {}
    config: dict[str, Any] = {}
    for key, value in data.items():
        normalized = key.replace("-", "_")
        if normalized == "json":
            normalized = "output_json"
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
        normalized_pattern = pattern.lower().replace("_", "-")
        if normalized_pattern == name:
            return mode
        if pattern.startswith("/") and pattern.endswith("/"):
            import re

            try:
                if re.search(pattern[1:-1], name):
                    return mode
            except re.error:
                continue
        if fnmatch.fnmatchcase(name, pattern.lower().replace("_", "-")):
            return mode
    return None
