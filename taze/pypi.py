from __future__ import annotations

import json
import time
import urllib.request
from urllib.error import URLError

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version


_USER_AGENT = "taze/0.1.1 (https://github.com/keksiqc/taze)"
_RETRY_DELAYS = (1.0, 3.0)  # seconds between attempts 1→2 and 2→3


def fetch_pypi_info(
    package: str,
    *,
    pre: bool = False,
    current_version: str | None = None,
    specifier: SpecifierSet | None = None,
    mode: str = "major",
) -> tuple[str | None, str | None, str | None]:
    """
    Return (latest_version, latest_release_date, current_release_date).

    Dates are YYYY-MM-DD strings. All three are None on failure.
    """
    url = f"https://pypi.org/pypi/{package}/json"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    data: dict | None = None
    for _attempt, delay in enumerate((*_RETRY_DELAYS, None)):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            break
        except URLError, OSError, ValueError:
            if delay is None:
                return None, None, None
            time.sleep(delay)
    if data is None:
        return None, None, None

    info_version: str = data.get("info", {}).get("version", "")
    releases: dict = data.get("releases", {})

    current_date = _upload_date(releases, current_version) if current_version else None

    # The registry's ``info.version`` is sufficient only when no policy needs
    # to inspect the release history. Range- and mode-aware resolution must
    # consider every non-yanked release.
    if not specifier and mode in ("major", "latest", "stable") and not pre and info_version:
        try:
            v = Version(info_version)
            if not v.is_prerelease and not v.is_devrelease:
                return str(v), _upload_date(releases, info_version), current_date
        except InvalidVersion:
            pass

    current = _as_version(current_version)
    best: Version | None = None
    for v_str, files in releases.items():
        if not files:
            continue
        if all(f.get("yanked") for f in files):
            continue
        try:
            v = Version(v_str)
        except InvalidVersion:
            continue
        if not pre and (v.is_prerelease or v.is_devrelease):
            continue
        if specifier and not specifier.contains(v, prereleases=pre):
            continue
        if current and not _within_mode(v, current, mode):
            continue
        if best is None or v > best:
            best = v

    if best is None:
        return None, None, current_date
    return str(best), _upload_date(releases, str(best)), current_date


def _as_version(value: str | None) -> Version | None:
    if not value:
        return None
    try:
        return Version(value)
    except InvalidVersion:
        return None


def _within_mode(candidate: Version, current: Version, mode: str) -> bool:
    """Return whether a candidate stays within the requested update ceiling."""
    if candidate <= current:
        return True
    if mode == "patch":
        return candidate.major == current.major and candidate.minor == current.minor
    if mode == "minor":
        return candidate.major == current.major
    return True


def _upload_date(releases: dict, version: str | None) -> str | None:
    if not version:
        return None
    files = releases.get(version) or releases.get(version.replace("-", "_")) or []
    for f in files:
        ts: str = f.get("upload_time", "")
        if ts:
            return ts[:10]  # YYYY-MM-DD
    return None
