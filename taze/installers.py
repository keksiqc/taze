"""Select the dependency installer used by a Python project."""

from __future__ import annotations

import tomllib
from pathlib import Path


def install_command(project_dir: Path) -> list[str]:
    """Return the lockfile-aware install command for a project directory."""
    if (project_dir / "uv.lock").is_file():
        return ["uv", "sync"]
    if (project_dir / "poetry.lock").is_file() or _uses_tool(project_dir, "poetry"):
        return ["poetry", "install"]
    if (project_dir / "pdm.lock").is_file() or _uses_tool(project_dir, "pdm"):
        return ["pdm", "install"]
    if (project_dir / "pixi.lock").is_file() or (project_dir / "pixi.toml").is_file():
        return ["pixi", "install"]
    return ["uv", "sync"]


def _uses_tool(project_dir: Path, tool: str) -> bool:
    pyproject = project_dir / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        with pyproject.open("rb") as f:
            return tool in tomllib.load(f).get("tool", {})
    except tomllib.TOMLDecodeError:
        return False
