"""Resolve GitHub Action versions and commit SHAs."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import urllib.request
from collections.abc import MutableMapping
from datetime import UTC, datetime
from urllib.error import URLError

import orjson
from packaging.version import InvalidVersion, Version

from taze.registries.pypi import normalise_version_ranges


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
    precise: bool = False,
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
        version = parse_action_version(tag)
        if version is None or (not pre and version.is_prerelease):
            continue
        sha = item.get("commit", {}).get("sha") if isinstance(item.get("commit"), dict) else None
        candidates.append((version, tag, sha if isinstance(sha, str) else None))
    known_tags = {item[1]: item[2] for item in candidates}

    current = parse_action_version(current_version)
    if current is not None:
        candidates = [item for item in candidates if item[0] > current]
    if maturity_period > 0:
        cutoff = time.time() - maturity_period * 24 * 60 * 60
        mature_excluded = normalise_version_ranges(maturity_exclude_ranges)
        candidates = [
            item
            for item in candidates
            if (mature_excluded and any(spec.contains(item[0], prereleases=True) for spec in mature_excluded))
            or not release_dates.get(item[1])
            or release_dates[item[1]] <= cutoff
        ]
    included = normalise_version_ranges(include_ranges)
    if included:
        candidates = [item for item in candidates if any(spec.contains(item[0], prereleases=True) for spec in included)]
    excluded = normalise_version_ranges(exclude_ranges)
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
        # Nothing newer, but still report the current tag's SHA so callers can
        # pin an already-up-to-date reference (e.g. --github-actions-pin).
        return current_version, None, None, known_tags.get(current_version or "")
    best_version, best_tag, best_sha = max(candidates, key=lambda item: item[0])
    target_tag = best_tag
    # A SHA-pinned write (pinact-style) wants the exact tag/commit, not the
    # floating major tag matching the current pin's granularity.
    if current_version and not precise:
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


def parse_action_version(value: str | None) -> Version | None:
    """Parse a version tag supported by GitHub Actions references."""
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
                data = orjson.loads(response.read())
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
