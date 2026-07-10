from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from packaging.specifiers import SpecifierSet

from taze.pypi import _upload_date, fetch_pypi_info


FAKE_RELEASES = {
    "1.0.0": [{"upload_time": "2022-01-01T12:00:00", "yanked": False}],
    "1.1.0": [{"upload_time": "2023-06-15T08:00:00", "yanked": False}],
    "2.0.0": [{"upload_time": "2024-03-10T10:00:00", "yanked": False}],
    "3.0.0a1": [{"upload_time": "2024-09-01T00:00:00", "yanked": False}],
    "0.9.0": [{"upload_time": "2021-05-05T00:00:00", "yanked": True}],
    "1.2.0": [],
}

FAKE_DATA = {
    "info": {"version": "2.0.0"},
    "releases": FAKE_RELEASES,
}


def _mock_urlopen(data: dict):
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    ctx.read = MagicMock(return_value=json.dumps(data).encode())
    return ctx


class TestFetchPypiInfo:
    def _fetch(self, data=None, **kw):
        d = data if data is not None else FAKE_DATA
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(d)):
            return fetch_pypi_info("requests", **kw)

    def test_returns_latest_stable(self) -> None:
        version, _, _ = self._fetch()
        assert version == "2.0.0"

    def test_skips_prerelease_by_default(self) -> None:
        version, _, _ = self._fetch()
        assert "a" not in (version or "")

    def test_includes_prerelease_with_pre(self) -> None:
        version, _, _ = self._fetch(pre=True)
        assert version == "3.0.0a1"

    def test_honours_declared_pep440_range(self) -> None:
        version, _, _ = self._fetch(specifier=SpecifierSet(">=1.0,<2.0"), mode="default")
        assert version == "1.1.0"

    def test_minor_mode_stays_in_current_major(self) -> None:
        version, _, _ = self._fetch(current_version="1.0.0", mode="minor")
        assert version == "1.1.0"

    def test_patch_mode_stays_in_current_minor(self) -> None:
        data = {
            "info": {"version": "1.2.0"},
            "releases": {
                "1.0.0": [{"upload_time": "2022-01-01T00:00:00", "yanked": False}],
                "1.0.1": [{"upload_time": "2022-01-02T00:00:00", "yanked": False}],
                "1.1.0": [{"upload_time": "2022-01-03T00:00:00", "yanked": False}],
            },
        }
        version, _, _ = self._fetch(data=data, current_version="1.0.0", mode="patch")
        assert version == "1.0.1"

    def test_skips_yanked_in_full_scan(self) -> None:
        # fast path trusts info.version; force full scan by leaving info.version empty
        data = {
            "info": {"version": ""},
            "releases": {"0.9.0": [{"upload_time": "2021-01-01T00:00:00", "yanked": True}]},
        }
        version, _, _ = self._fetch(data=data)
        assert version is None

    def test_skips_empty_release_in_full_scan(self) -> None:
        data = {
            "info": {"version": ""},
            "releases": {"1.2.0": []},
        }
        version, _, _ = self._fetch(data=data)
        assert version is None

    def test_returns_release_date(self) -> None:
        _, latest_date, _ = self._fetch()
        assert latest_date == "2024-03-10"

    def test_returns_current_date(self) -> None:
        _, _, current_date = self._fetch(current_version="1.1.0")
        assert current_date == "2023-06-15"

    def test_current_date_none_when_not_provided(self) -> None:
        _, _, current_date = self._fetch()
        assert current_date is None

    def test_returns_none_on_network_error(self) -> None:
        from urllib.error import URLError

        with patch("urllib.request.urlopen", side_effect=URLError("timeout")), patch("time.sleep"):
            result = fetch_pypi_info("requests")
        assert result == (None, None, None)

    def test_retries_on_network_error(self) -> None:
        from urllib.error import URLError

        call_count = 0

        def urlopen_side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                msg = "timeout"
                raise URLError(msg)
            return _mock_urlopen(FAKE_DATA)

        with patch("urllib.request.urlopen", side_effect=urlopen_side_effect), patch("time.sleep") as mock_sleep:
            version, _, _ = fetch_pypi_info("requests")

        assert version == "2.0.0"
        assert call_count == 3
        assert mock_sleep.call_count == 2


class TestUploadDate:
    def test_known_version(self) -> None:
        assert _upload_date(FAKE_RELEASES, "1.0.0") == "2022-01-01"

    def test_unknown_version(self) -> None:
        assert _upload_date(FAKE_RELEASES, "9.9.9") is None

    def test_none_version(self) -> None:
        assert _upload_date(FAKE_RELEASES, None) is None

    def test_empty_files(self) -> None:
        assert _upload_date({"1.0.0": []}, "1.0.0") is None
