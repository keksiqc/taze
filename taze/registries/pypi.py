"""Resolve package versions from PyPI."""

from __future__ import annotations

import re
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import MutableMapping
from datetime import UTC, date, datetime
from urllib.error import URLError

import msgspec
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from taze import __version__


_USER_AGENT = f"taze/{__version__} (https://github.com/keksiqc/taze)"
_RETRY_DELAYS = (1.0, 3.0)  # seconds between attempts 1→2 and 2→3


class _PypiFile(msgspec.Struct):
    """The subset of a PyPI release file we read, decoded straight off the wire."""

    requires_python: str | None = None
    upload_time: str | None = None
    upload_time_iso_8601: str | None = None
    yanked: bool | None = None


class _PypiInfo(msgspec.Struct):
    version: str | None = None
    requires_python: str | None = None


class _PypiResponse(msgspec.Struct):
    """Schema for PyPI's ``/pypi/<name>/json`` response (untrusted, network-sourced)."""

    info: _PypiInfo = msgspec.field(default_factory=_PypiInfo)
    releases: dict[str, list[_PypiFile] | None] = msgspec.field(default_factory=dict)


def fetch_pypi_info(
    package: str,
    *,
    pre: bool = False,
    current_version: str | None = None,
    specifier: SpecifierSet | None = None,
    mode: str = "major",
    maturity_period: int = 0,
    exclude_ranges: tuple[str, ...] = (),
    include_ranges: tuple[str, ...] = (),
    maturity_exclude_ranges: tuple[str, ...] = (),
    timeout: float = 10.0,
    retries: int = 2,
    cache: MutableMapping[str, dict] | None = None,
    force: bool = False,
) -> tuple[str | None, str | None, str | None]:
    """
    Return ``(latest_version, latest_release_date, current_release_date)``.

    ``cache`` is deliberately an optional raw-response mapping: callers that
    need a persistent cache can load/save it around a scan, while direct use
    remains deterministic and easy to test.
    """
    data = None if force else _cached(cache, package)
    if data is None:
        data = _request(package, timeout=timeout, retries=max(0, retries))
        if data is None:
            return None, None, None
        if cache is not None:
            cache[package] = data

    if not isinstance(data, dict):
        return None, None, None

    info = data.get("info", {})
    releases = data.get("releases", {})
    if not isinstance(info, dict) or not isinstance(releases, dict):
        return None, None, None

    info_version = info.get("version", "") if isinstance(info.get("version", ""), str) else ""
    current_date = _upload_date(releases, current_version) if current_version else None
    excluded = normalise_version_ranges(exclude_ranges)
    included = normalise_version_ranges(include_ranges)
    maturity_excluded = normalise_version_ranges(maturity_exclude_ranges)
    current = _as_version(current_version)

    # The registry's ``info.version`` is sufficient only when no policy needs
    # to inspect release history. Range- and mode-aware resolution scans all
    # non-yanked releases instead.
    if (
        not specifier
        and not maturity_period
        and not excluded
        and not included
        and mode in ("major", "latest")
        and not pre
        and info_version
        and _python_compatible(releases.get(info_version, []), info.get("requires_python"))
    ):
        try:
            version = Version(info_version)
            if (
                not version.is_prerelease
                and not version.is_devrelease
                and (not current_version or _within_mode(version, _as_version(current_version), mode))
                and (current is None or version > current)
            ):
                return str(version), _upload_date(releases, info_version), current_date
        except InvalidVersion:
            pass

    best: Version | None = None
    for version_string, files in releases.items():
        if not isinstance(version_string, str) or not isinstance(files, list) or not files:
            continue
        if all(isinstance(file, dict) and file.get("yanked") for file in files):
            continue
        try:
            version = Version(version_string)
        except InvalidVersion:
            continue
        if not pre and (version.is_prerelease or version.is_devrelease):
            continue
        if not _python_compatible(files, info.get("requires_python") if version_string == info_version else None):
            continue
        if included and not _version_in_ranges(version, included):
            continue
        if excluded and _version_in_ranges(version, excluded):
            continue
        if (
            maturity_period
            and not _version_in_ranges(version, maturity_excluded)
            and not _is_mature(files, maturity_period)
        ):
            continue
        if specifier and not specifier.contains(version, prereleases=pre):
            continue
        if current and (version <= current or not _within_mode(version, current, mode)):
            continue
        if best is None or version > best:
            best = version

    if best is None:
        return current_version, current_date, current_date if current_version else None
    best_string = str(best)
    return best_string, _upload_date(releases, best_string), current_date


def _cached(cache: MutableMapping[str, dict] | None, package: str) -> dict | None:
    if cache is None:
        return None
    data = cache.get(package)
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
        return data["data"]
    return data if isinstance(data, dict) else None


def _request(package: str, *, timeout: float, retries: int) -> dict | None:
    encoded = urllib.parse.quote(package, safe="")
    url = f"https://pypi.org/pypi/{encoded}/json"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                parsed = msgspec.json.decode(resp.read(), type=_PypiResponse, strict=False)
            return _slim(parsed)
        except (URLError, OSError, ValueError, msgspec.DecodeError, msgspec.ValidationError):
            if attempt >= retries:
                return None
            delay = _RETRY_DELAYS[attempt] if attempt < len(_RETRY_DELAYS) else _RETRY_DELAYS[-1] * 2 ** (attempt - 1)
            time.sleep(delay)
    return None


def _slim(data: _PypiResponse) -> dict:
    """Drop the per-file hashes/URLs/sizes PyPI's JSON API includes but we never read.

    A full response can run into the megabytes per package (every hash digest,
    download URL, and filename for every file of every release ever
    published), which slows down both persisting and re-parsing the local
    cache. Keeping only the fields ``fetch_pypi_info`` actually consumes cuts
    that down by roughly an order of magnitude.
    """
    slim_releases: dict[str, list[dict]] = {
        version: [
            {
                "requires_python": file.requires_python,
                "upload_time": file.upload_time or file.upload_time_iso_8601,
                "yanked": bool(file.yanked),
            }
            for file in files
        ]
        for version, files in data.releases.items()
        if files
    }
    return {
        "info": {"version": data.info.version, "requires_python": data.info.requires_python},
        "releases": slim_releases,
    }


def normalise_version_ranges(ranges: tuple[str, ...] | list[str]) -> tuple[SpecifierSet, ...]:
    """Convert upstream-style selectors such as ``7`` and ``^2`` to PEP 440."""
    result: list[SpecifierSet] = []
    for raw in ranges:
        for value in raw.split("||"):
            value = value.strip()
            if not value:
                continue
            converted = _normalise_range(value)
            if converted is None:
                continue
            try:
                result.append(SpecifierSet(converted))
            except InvalidSpecifier:
                continue
    return tuple(result)


def _normalise_range(value: str) -> str | None:
    value = value.strip()
    if value.startswith("^"):
        return _caret_range(value[1:])
    if value.startswith("~") and not value.startswith("~="):
        return _tilde_range(value[1:])
    if re.fullmatch(r"v?\d+(?:\.\d+){0,2}", value):
        parts = value.lstrip("v").split(".")
        if len(parts) == 1:
            return f">={value.lstrip('v')},<{int(parts[0]) + 1}"
        if len(parts) == 2:
            return f">={value.lstrip('v')},<{parts[0]}.{int(parts[1]) + 1}"
        return f"=={value.lstrip('v')}"
    return value


def _caret_range(value: str) -> str | None:
    try:
        base = Version(value)
    except InvalidVersion:
        return None
    if base.major:
        upper = f"{base.major + 1}.0"
    elif base.minor:
        upper = f"0.{base.minor + 1}"
    else:
        upper = f"0.0.{base.micro + 1}"
    return f">={base},<{upper}"


def _tilde_range(value: str) -> str | None:
    try:
        base = Version(value)
    except InvalidVersion:
        return None
    return f">={base},<{base.major}.{base.minor + 1}"


def _version_in_ranges(version: Version, ranges: tuple[SpecifierSet, ...]) -> bool:
    return any(specifier.contains(version, prereleases=True) for specifier in ranges)


def _python_compatible(files: object, fallback: object = None) -> bool:
    """Reject releases that cannot run on the interpreter doing the check."""
    if not isinstance(files, list):
        return _python_requirement_compatible(fallback)
    current = Version(".".join(str(part) for part in sys.version_info[:3]))
    requirements: list[str] = []
    unconstrained = False
    for file in files:
        if not isinstance(file, dict):
            continue
        requirement = file.get("requires_python")
        if not requirement:
            unconstrained = True
        elif isinstance(requirement, str):
            requirements.append(requirement)
    if not requirements:
        return _python_requirement_compatible(fallback) if fallback else True
    return unconstrained or any(_python_requirement_compatible(requirement, current) for requirement in requirements)


def _python_requirement_compatible(requirement: object, current: Version | None = None) -> bool:
    if not isinstance(requirement, str) or not requirement:
        return True
    try:
        version = current or Version(".".join(str(part) for part in sys.version_info[:3]))
        return SpecifierSet(requirement).contains(version, prereleases=True)
    except InvalidSpecifier:
        return True


def _as_version(value: str | None) -> Version | None:
    if not value:
        return None
    try:
        return Version(value.lstrip("vV"))
    except InvalidVersion:
        return None


def _within_mode(candidate: Version, current: Version | None, mode: str) -> bool:
    """Return whether a candidate stays within the requested update ceiling."""
    if current is None or candidate <= current:
        return True
    if mode == "patch":
        return candidate.major == current.major and candidate.minor == current.minor
    if mode in ("minor", "default", "stable"):
        return candidate.major == current.major if mode == "minor" else True
    return True


def _is_mature(files: list[dict], period: int, *, today: date | None = None) -> bool:
    """Whether a release has been available for at least ``period`` days."""
    if period <= 0:
        return True
    published = _upload_date({"release": files}, "release")
    if not published:
        return False
    try:
        released = date.fromisoformat(published)
    except ValueError:
        return False
    return ((today or datetime.now(tz=UTC).date()) - released).days >= period


def _upload_date(releases: dict, version: str | None) -> str | None:
    if not version:
        return None
    files = releases.get(version) or releases.get(version.replace("-", "_")) or []
    for file in files:
        if not isinstance(file, dict):
            continue
        timestamp = file.get("upload_time") or file.get("upload_time_iso_8601") or ""
        if timestamp:
            return str(timestamp)[:10]
    return None
