"""GitHub Actions references, kept dependency-free for the Python port."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from collections.abc import MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError

from packaging.version import InvalidVersion, Version

from taze.models import DepInfo


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
    except OSError, UnicodeError:
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
    if not ref.lower().startswith("v") or _parse_version(ref) is None:
        return None
    return repo, subpath, ref, "tag", None


def fetch_github_action_info(
    repo: str,
    *,
    current_version: str | None,
    mode: str = "major",
    pre: bool = False,
    maturity_period: int = 0,
    exclude_ranges: tuple[str, ...] = (),
    include_ranges: tuple[str, ...] = (),
    maturity_exclude_ranges: tuple[str, ...] = (),
    timeout: float = 10.0,
    retries: int = 2,
    cache: MutableMapping[str, list[dict]] | None = None,
    force: bool = False,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Return the best version tag and commit SHA for an action repository."""
    key = f"github:{repo}"
    data = None if force else (cache.get(key) if cache else None)
    if data is None:
        data = _request_tags(repo, timeout=timeout, retries=max(0, retries))
        if data is None:
            return None, None, None, None
        if cache is not None:
            cache[key] = data

    release_dates = _release_dates(repo, timeout=timeout, retries=max(0, retries)) if maturity_period > 0 else {}
    candidates: list[tuple[Version, str, str | None]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        tag = item.get("name")
        if not isinstance(tag, str) or not tag.lower().startswith("v"):
            continue
        version = _parse_version(tag)
        if version is None or (not pre and version.is_prerelease):
            continue
        sha = item.get("commit", {}).get("sha") if isinstance(item.get("commit"), dict) else None
        candidates.append((version, tag, sha if isinstance(sha, str) else None))
    known_tags = {item[1]: item[2] for item in candidates}

    current = _parse_version(current_version)
    if current is not None:
        candidates = [item for item in candidates if item[0] > current]
    if maturity_period > 0:
        cutoff = time.time() - maturity_period * 24 * 60 * 60
        mature_excluded = _exclude_versions(maturity_exclude_ranges)
        candidates = [
            item
            for item in candidates
            if (mature_excluded and any(spec.contains(item[0], prereleases=True) for spec in mature_excluded))
            or not release_dates.get(item[1])
            or release_dates[item[1]] <= cutoff
        ]
    included = _exclude_versions(include_ranges)
    if included:
        candidates = [item for item in candidates if any(spec.contains(item[0], prereleases=True) for spec in included)]
    excluded = _exclude_versions(exclude_ranges)
    if excluded:
        candidates = [
            item for item in candidates if not any(spec.contains(item[0], prereleases=True) for spec in excluded)
        ]

    if current is not None:
        effective_mode = mode
        if mode in ("default", "stable"):
            effective_mode = "minor"
        if effective_mode == "patch":
            candidates = [
                item for item in candidates if item[0].major == current.major and item[0].minor == current.minor
            ]
        elif effective_mode == "minor":
            candidates = [item for item in candidates if item[0].major == current.major]

    if not candidates:
        return current_version, None, None, None
    best_version, best_tag, best_sha = max(candidates, key=lambda item: item[0])
    target_tag = best_tag
    if current_version:
        parts = current_version.lstrip("vV").split(".")
        desired_parts = [str(best_version.major)]
        if len(parts) >= 2:
            desired_parts.append(str(best_version.minor))
        if len(parts) >= 3:
            desired_parts.append(str(best_version.micro))
        desired = "v" + ".".join(desired_parts)
        if desired in known_tags:
            target_tag = desired
            best_sha = known_tags[desired] or best_sha
    return (
        target_tag,
        _release_date(release_dates.get(best_tag)),
        _release_date(release_dates.get(current_version or "")),
        best_sha,
    )


def _request_tags(repo: str, *, timeout: float, retries: int) -> list[dict] | None:
    tags: list[dict] = []
    for page in range(1, 6):
        data = _request_json(
            f"https://api.github.com/repos/{repo}/tags?per_page=100&page={page}",
            timeout=timeout,
            retries=retries,
        )
        if not data:
            break
        tags.extend(data)
        if len(data) < 100:
            break
    return tags or None


def _release_dates(repo: str, *, timeout: float, retries: int) -> dict[str, float]:
    data = _request_json(
        f"https://api.github.com/repos/{repo}/releases?per_page=100",
        timeout=timeout,
        retries=retries,
    )
    if not data:
        return {}
    result: dict[str, float] = {}
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("tag_name"), str):
            continue
        published = item.get("published_at") or item.get("created_at")
        if not isinstance(published, str):
            continue
        try:
            result[item["tag_name"]] = datetime.fromisoformat(published).timestamp()
        except OverflowError, ValueError:
            continue
    return result


def _release_date(timestamp: float | None) -> str | None:
    return datetime.fromtimestamp(timestamp, tz=UTC).date().isoformat() if timestamp is not None else None


def _request_json(url: str, *, timeout: float, retries: int) -> list[dict] | None:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "taze"}
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read())
            return data if isinstance(data, list) else None
        except URLError, OSError, ValueError:
            if attempt >= retries:
                return None
            time.sleep(1.0 if attempt == 0 else 3.0)
    return None


def _github_token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    if not shutil.which("gh"):
        return None
    try:
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False, timeout=2)
    except OSError, subprocess.SubprocessError:
        return None
    return result.stdout.strip() or None


def _parse_version(value: str | None) -> Version | None:
    if not value:
        return None
    value = value.lstrip("vV")
    if not re.fullmatch(r"\d+(?:\.\d+){0,2}(?:[-+][A-Za-z0-9.-]+)?", value):
        return None
    if re.fullmatch(r"\d+(?:\.\d+){0,1}", value):
        value += ".0" * (3 - value.count(".") - 1)
    try:
        return Version(value)
    except InvalidVersion:
        return None


def _version_tag(value: str) -> str:
    return value if value.lower().startswith("v") else f"v{value}"


def _exclude_versions(ranges: tuple[str, ...]):
    from taze.pypi import normalise_version_ranges

    return normalise_version_ranges(ranges)


def write_action_updates(
    path: Path,
    infos: list[DepInfo],
    *,
    mode: str = "major",
    style: str = "auto",
) -> int:
    """Update action refs in place while preserving YAML indentation/comments."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError, UnicodeError:
        return 0
    count = 0
    for info in infos:
        if not info.is_shown(mode) or info.line_number is None or not info.latest:
            continue
        index = info.line_number - 1
        if not 0 <= index < len(lines):
            continue
        match = _ACTION_LINE.match(lines[index].rstrip("\r\n"))
        if not match:
            continue
        effective = info.action_style if style == "auto" else style
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
