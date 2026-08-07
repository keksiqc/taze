"""Project discovery helpers with workspace-aware exclusions."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path


DEFAULT_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    ".tox",
    ".nox",
    "__pycache__",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "public",
    "fixture",
    "fixtures",
    "build",
    "dist",
}


def discover_files(
    root: Path,
    *,
    recursive: bool = False,
    ignore_paths: tuple[str, ...] = (),
    ignore_other_workspaces: bool = True,
    github_actions: bool = True,
) -> list[Path]:
    """Find supported dependency files, without descending into ignored workspaces."""
    root = root.resolve()
    if not recursive:
        return _files_in(root, github_actions=github_actions)

    found: list[Path] = []
    for current, dirs, _files in os.walk(root):
        directory = Path(current)
        rel = directory.relative_to(root)
        dirs[:] = [
            d
            for d in dirs
            if not _ignored(rel / d, ignore_paths)
            and not (ignore_other_workspaces and _is_workspace_root(directory / d))
        ]
        found.extend(_files_in(directory, github_actions=github_actions))
    return sorted(
        {path for path in found if not _ignored(path.relative_to(root), ignore_paths)},
    )


def _files_in(directory: Path, *, github_actions: bool = True) -> list[Path]:
    files: list[Path] = []
    pyproject = directory / "pyproject.toml"
    if pyproject.is_file():
        files.append(pyproject)
    files.extend(sorted(p for p in directory.glob("requirements*.txt") if p.is_file()))
    if github_actions:
        files.extend(
            p
            for pattern in (".github/workflows/*.yml", ".github/workflows/*.yaml")
            for p in directory.glob(pattern)
            if p.is_file()
        )
        files.extend(
            p
            for pattern in (".github/actions/**/action.yml", ".github/actions/**/action.yaml")
            for p in directory.glob(pattern)
            if p.is_file()
        )
        for name in ("action.yml", "action.yaml"):
            action = directory / name
            if action.is_file():
                files.append(action)
    return files


def _ignored(relative: Path, patterns: tuple[str, ...]) -> bool:
    path = relative.as_posix()
    return relative.name in DEFAULT_IGNORED_DIRS or any(
        fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(f"{path}/", pattern) for pattern in patterns
    )


def _is_workspace_root(path: Path) -> bool:
    return (path / ".git").exists() or (path / "pnpm-workspace.yaml").is_file()
