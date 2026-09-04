"""Read and update GitHub Actions references in YAML files."""

from __future__ import annotations

import re
from pathlib import Path

from taze.models import DepInfo
from taze.registries.github import parse_action_version


_ACTION_LINE = re.compile(
    r"^(?P<prefix>\s*(?:-\s*)?(?:uses|['\"]uses['\"])\s*:\s*)(?P<quote>['\"]?)(?P<value>[^'\"\s#]+)(?P=quote)(?P<comment>\s+#.*)?$",
)
_VERSION_RE = re.compile(r"\bv?\d+(?:\.\d+){0,2}(?:[-+][A-Za-z0-9.-]+)?\b", re.IGNORECASE)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


def is_action_file(path: Path) -> bool:
    relative = path.as_posix()
    return (
        re.search(r"(?:^|/)\.github/workflows/[^/]+\.ya?ml$", relative) is not None
        or re.search(r"(?:^|/)\.github/actions/(?:.*/)?action\.ya?ml$", relative) is not None
        or bool(re.search(r"(?:^|/)action\.ya?ml$", relative))
    )


def parse_actions(path: Path) -> list[DepInfo]:
    """Parse versioned ``uses:`` entries without reserialising the YAML file."""
    # ponytail: line parser preserves YAML formatting; use a YAML parser if
    # flow-style or folded ``uses`` values need to be supported.
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []

    result: list[DepInfo] = []
    for line_number, line in enumerate(lines, 1):
        match = _ACTION_LINE.match(line)
        if not match:
            continue
        parsed = _parse_reference(match.group("value"), match.group("comment"))
        if not parsed:
            continue
        repo, subpath, version, style, sha = parsed
        result.append(
            DepInfo(
                raw=match.group("value"),
                name=repo.lower(),
                current=version,
                operator=None,
                line_number=line_number,
                source="github-actions",
                action_repo=repo,
                action_subpath=subpath,
                action_style=style,
                action_sha=sha,
            ),
        )
    return result


def _parse_reference(value: str, comment: str | None) -> tuple[str, str, str, str, str | None] | None:
    if value.startswith(("./", "../", "docker://")) or "@" not in value:
        return None
    target, ref = value.rsplit("@", 1)
    parts = target.split("/")
    if len(parts) < 2 or not all(parts[:2]):
        return None
    repo = "/".join(parts[:2])
    subpath = "/" + "/".join(parts[2:]) if len(parts) > 2 else ""

    if _SHA_RE.fullmatch(ref):
        match = _VERSION_RE.search(comment or "")
        if not match:
            return None
        return repo, subpath, _version_tag(match.group()), "sha", ref
    if not ref.lower().startswith("v") or parse_action_version(ref) is None:
        return None
    return repo, subpath, ref, "tag", None


def _version_tag(value: str) -> str:
    return value if value.lower().startswith("v") else f"v{value}"


def write_action_updates(
    path: Path,
    infos: list[DepInfo],
    *,
    mode: str = "major",
    style: str = "auto",
    pin_unchanged: bool = False,
) -> int:
    """Update action refs in place while preserving YAML indentation/comments."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeError):
        return 0
    count = 0
    for info in infos:
        if info.line_number is None or not info.latest:
            continue
        effective = info.action_style if style == "auto" else style
        # Even if already up to date, --github-actions-pin converts a tag pin
        # to a SHA pin (pinact-style), since that's not a version bump to skip.
        convert_only = pin_unchanged and effective == "sha" and info.action_style != "sha"
        if not info.is_shown(mode) and not convert_only:
            continue
        index = info.line_number - 1
        if not 0 <= index < len(lines):
            continue
        match = _ACTION_LINE.match(lines[index].rstrip("\r\n"))
        if not match:
            continue
        if effective == "sha":
            reference = info.action_target_sha
            if not reference:
                continue
            comment = match.group("comment") or ""
            if re.search(r"\bv\d", comment, re.IGNORECASE):
                comment = re.sub(r"\bv\d[^\s#]*", info.latest, comment, count=1, flags=re.IGNORECASE)
            else:
                comment = f" # {info.latest}"
        else:
            reference = _preserve_granularity(info.current or "", info.latest)
            comment = re.sub(r"\s+#\s*v\d[^\n]*", "", match.group("comment") or "", count=1, flags=re.IGNORECASE)
        value = f"{info.action_repo}{info.action_subpath}@{reference}"
        ending = lines[index][len(lines[index].rstrip("\r\n")) :]
        lines[index] = (
            f"{match.group('prefix')}{match.group('quote')}{value}{match.group('quote')}{comment or ''}{ending}"
        )
        count += 1
    if count:
        path.write_text("".join(lines), encoding="utf-8")
    return count


def _preserve_granularity(current: str, target: str) -> str:
    current_parts = current.lstrip("vV").split(".")
    target_parts = target.lstrip("vV").split(".")
    count = min(max(len(current_parts), 1), len(target_parts))
    return "v" + ".".join(target_parts[:count])
