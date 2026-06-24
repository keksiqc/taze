from __future__ import annotations

import json
import urllib.request
from urllib.error import URLError

from packaging.version import Version, InvalidVersion

_USER_AGENT = "taze/0.1.0 (https://github.com/keksi/taze)"


def fetch_pypi_info(
    package: str,
    *,
    pre: bool = False,
    current_version: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """
    Return (latest_version, latest_release_date, current_release_date).
    Dates are YYYY-MM-DD strings. All three are None on failure.
    """
    try:
        url = f"https://pypi.org/pypi/{package}/json"
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except (URLError, OSError, ValueError):
        return None, None, None

    info_version: str = data.get("info", {}).get("version", "")
    releases: dict = data.get("releases", {})

    current_date = _upload_date(releases, current_version) if current_version else None

    # Fast path: trust info.version for stable-only queries
    if not pre and info_version:
        try:
            v = Version(info_version)
            if not v.is_prerelease and not v.is_devrelease:
                return str(v), _upload_date(releases, info_version), current_date
        except InvalidVersion:
            pass

    # Full scan — needed for --pre modes or when info.version is a pre-release
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
        if best is None or v > best:
            best = v

    if best is None:
        return None, None, current_date
    return str(best), _upload_date(releases, str(best)), current_date


def _upload_date(releases: dict, version: str | None) -> str | None:
    if not version:
        return None
    files = releases.get(version) or releases.get(version.replace("-", "_")) or []
    for f in files:
        ts: str = f.get("upload_time", "")
        if ts:
            return ts[:10]  # YYYY-MM-DD
    return None
