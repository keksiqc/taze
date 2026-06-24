from __future__ import annotations

import re
from pathlib import Path

from .models import DepInfo


def write_pyproject_updates(path: Path, all_infos: dict[str, list[DepInfo]]) -> int:
    """Replace outdated dep strings in pyproject.toml. Returns number of changes."""
    content = path.read_text(encoding="utf-8")
    count = 0

    for _label, infos in all_infos.items():
        for info in infos:
            if not info.is_outdated:
                continue
            new_raw = info.updated_raw()
            if new_raw == info.raw:
                continue
            # Handle both single and double-quoted TOML strings
            for q in ('"', "'"):
                old_quoted = re.escape(f"{q}{info.raw}{q}")
                new_content = re.sub(old_quoted, f"{q}{new_raw}{q}", content)
                if new_content != content:
                    content = new_content
                    count += 1
                    break

    path.write_text(content, encoding="utf-8")
    return count


def write_requirements_updates(path: Path, infos: list[DepInfo]) -> int:
    """Update version specs in a requirements.txt file. Returns number of changes."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    count = 0

    for info in infos:
        if not info.is_outdated or info.line_number is None:
            continue
        new_raw = info.updated_raw()
        if new_raw == info.raw:
            continue
        idx = info.line_number - 1
        if idx >= len(lines):
            continue
        old_line = lines[idx]
        # Preserve trailing comment and line ending
        tail = re.search(r"(\s+#.*)$", old_line.rstrip("\n\r"))
        comment = tail.group(1) if tail else ""
        ending = old_line[len(old_line.rstrip("\n\r")):]
        lines[idx] = new_raw + comment + ending
        count += 1

    path.write_text("".join(lines), encoding="utf-8")
    return count
