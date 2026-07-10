"""Project discovery helpers with workspace-aware exclusions."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path


DEFAULT_IGNORED_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "__pycache__", ".cache", "build", "dist"}


def discover_files(
    root: Path,
    *,
    recursive: bool = False,
    ignore_paths: tuple[str, ...] = (),
    ignore_other_workspaces: bool = True,
) -> list[Path]:
    """Find supported dependency files, without descending into ignored workspaces."""
    root = root.resolve()
    if not recursive:
        return _files_in(root)

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
        found.extend(_files_in(directory))
    return sorted(set(found))


def _files_in(directory: Path) -> list[Path]:
    files: list[Path] = []
    pyproject = directory / "pyproject.toml"
    if pyproject.is_file():
        files.append(pyproject)
    files.extend(sorted(p for p in directory.glob("requirements*.txt") if p.is_file()))
    return files


def _ignored(relative: Path, patterns: tuple[str, ...]) -> bool:
    path = relative.as_posix()
    return relative.name in DEFAULT_IGNORED_DIRS or any(
        fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(f"{path}/", pattern) for pattern in patterns
    )


def _is_workspace_root(path: Path) -> bool:
    return (path / ".git").exists() or (path / "pnpm-workspace.yaml").is_file()
