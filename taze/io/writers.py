"""Write dependency updates while preserving source formatting."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from pathlib import Path

    from taze.models import DepInfo


def write_pyproject_updates(path: Path, all_infos: dict[str, list[DepInfo]], *, mode: str = "major") -> int:
    """Replace outdated dependency strings while preserving TOML formatting."""
    content = path.read_text(encoding="utf-8")
    count = 0

    for infos in all_infos.values():
        for info in infos:
            if not info.is_shown(mode) or not info.latest:
                continue
            if info.toml_section and info.toml_key and info.toml_value is not None:
                new_value = _updated_toml_value(info)
                if new_value != info.toml_value:
                    new_content = _replace_toml_value(
                        content, info.toml_section, info.toml_key, info.toml_value, new_value
                    )
                    if new_content != content:
                        content = new_content
                        count += 1
                continue
            new_raw = info.updated_raw()
            if new_raw == info.raw:
                continue
            # Replace one occurrence per parsed dependency. This handles the
            # same requirement appearing in two groups without overcounting.
            for quote in ('"', "'"):
                pattern = re.compile(rf"{re.escape(quote + info.raw + quote)}")
                new_content, changed = pattern.subn(f"{quote}{new_raw}{quote}", content, count=1)
                if changed:
                    content = new_content
                    count += 1
                    break

    path.write_text(content, encoding="utf-8")
    return count


def _updated_toml_value(info: DepInfo) -> str:
    """Update a Poetry value and retain its ``^``/``~`` style."""
    original = info.toml_value or ""
    prefix = original[:1] if original[:1] in "^~" else ""
    parts = info.latest.split(".") if info.latest else []
    count = len(re.findall(r"\d+", original))
    if prefix:
        return prefix + ".".join(parts[: max(1, min(count, len(parts)))])
    if re.fullmatch(r"v?\d+(?:\.\d+){0,2}", original):
        return ".".join(parts[: max(1, min(count, len(parts)))])
    return info.updated_raw().split(info.name, 1)[-1].lstrip()


def _replace_toml_value(content: str, section: str, key: str, old: str, new: str) -> str:
    section_pattern = re.escape(section)
    key_pattern = rf"(?:{re.escape(key)}|['\"]{re.escape(key)}['\"])"
    pattern = re.compile(
        rf"(?ms)(^\[{section_pattern}\]\s*$.*?)(?=^\[|\Z)",
    )
    match = pattern.search(content)
    if not match:
        return content
    block = match.group(1)
    value_pattern = re.compile(rf"(^\s*{key_pattern}\s*=\s*)(?P<quote>['\"]){re.escape(old)}(?P=quote)", re.MULTILINE)
    updated, count = value_pattern.subn(
        lambda item: f"{item.group(1)}{item.group('quote')}{new}{item.group('quote')}", block, count=1
    )
    if not count:
        inline_pattern = re.compile(
            rf"(^\s*{key_pattern}\s*=\s*\{{[^\n]*?version\s*=\s*)(?P<quote>['\"]){re.escape(old)}(?P=quote)",
            re.MULTILINE,
        )
        updated, count = inline_pattern.subn(
            lambda item: f"{item.group(1)}{item.group('quote')}{new}{item.group('quote')}", block, count=1
        )
    return content[: match.start(1)] + updated + content[match.end(1) :] if count else content


def write_requirements_updates(path: Path, infos: list[DepInfo], *, mode: str = "major") -> int:
    """Update version specs in a requirements.txt file. Returns number of changes."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    count = 0

    for info in infos:
        if not info.is_shown(mode) or info.line_number is None:
            continue
        new_raw = info.updated_raw()
        if new_raw == info.raw:
            continue
        idx = info.line_number - 1
        if idx < 0 or idx >= len(lines):
            continue
        old_line = lines[idx]
        # Preserve indentation, trailing comment and line ending.
        body = old_line.rstrip("\n\r")
        prefix = body[: len(body) - len(body.lstrip())]
        tail = re.search(r"(\s+#.*)$", body)
        comment = tail.group(1) if tail else ""
        ending = old_line[len(body) :]
        lines[idx] = prefix + new_raw + comment + ending
        count += 1

    path.write_text("".join(lines), encoding="utf-8")
    return count
