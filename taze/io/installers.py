"""Select external install commands for Python projects."""

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
    if not (project_dir / "pyproject.toml").is_file():
        requirement = project_dir / "requirements.txt"
        if not requirement.is_file():
            requirement = next(iter(sorted(project_dir.glob("requirements*.txt"))), None)
        if requirement:
            return ["uv", "pip", "install", "-r", requirement.name]
    return ["uv", "sync"]


def _uses_tool(project_dir: Path, tool: str) -> bool:
    pyproject = project_dir / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
        tools = data.get("tool", {})
        return isinstance(tools, dict) and tool in tools
    except OSError, tomllib.TOMLDecodeError, TypeError:
        return False
