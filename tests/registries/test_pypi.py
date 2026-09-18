from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from taze.registries.pypi import _is_mature, _upload_date, fetch_pypi_info, minimum_python


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

    def test_maturity_period_skips_recent_releases(self) -> None:
        data = {
            "info": {"version": "2.0.0"},
            "releases": {
                "1.0.0": [{"upload_time": "2024-01-01T00:00:00", "yanked": False}],
                "2.0.0": [{"upload_time": "2024-01-15T00:00:00", "yanked": False}],
            },
        }
        with patch("taze.registries.pypi.datetime") as mock_datetime:
            mock_datetime.now.return_value.date.return_value = date(2024, 1, 20)
            version, _, _ = self._fetch(data=data, maturity_period=7)
        assert version == "1.0.0"

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

    def test_skips_release_incompatible_with_current_python(self) -> None:
        data = {
            "info": {"version": "2.0.0", "requires_python": ">=99"},
            "releases": {
                "1.0.0": [{"upload_time": "2024-01-01T00:00:00", "requires_python": ">=3.0"}],
                "2.0.0": [{"upload_time": "2024-01-01T00:00:00", "requires_python": ">=99"}],
            },
        }
        version, _, _ = self._fetch(data=data)
        assert version == "1.0.0"

    def test_uses_project_python_instead_of_interpreter(self) -> None:
        data = {
            "info": {"version": "2.0.0", "requires_python": ">=3.12"},
            "releases": {
                "1.0.0": [{"upload_time": "2024-01-01T00:00:00", "requires_python": ">=3.9"}],
                "2.0.0": [{"upload_time": "2024-01-01T00:00:00", "requires_python": ">=3.12"}],
            },
        }
        version, _, _ = self._fetch(data=data, python_version=Version("3.10"))
        assert version == "1.0.0"
        version, _, _ = self._fetch(data=data, python_version=Version("3.12"))
        assert version == "2.0.0"

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

    def test_does_not_retry_unknown_package(self) -> None:
        import io
        from email.message import Message
        from urllib.error import HTTPError

        def not_found(request, timeout=None):
            raise HTTPError(request.full_url, 404, "Not Found", Message(), io.BytesIO(b""))

        with patch("urllib.request.urlopen", side_effect=not_found) as urlopen, patch("time.sleep") as mock_sleep:
            result = fetch_pypi_info("definitely-not-a-package", retries=2)

        assert result == (None, None, None)
        assert urlopen.call_count == 1
        mock_sleep.assert_not_called()

    def test_retries_server_errors_honouring_retry_after(self) -> None:
        import io
        from email.message import Message
        from urllib.error import HTTPError

        calls = 0

        def flaky(request, timeout=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                headers = Message()
                headers["Retry-After"] = "7"
                raise HTTPError(request.full_url, 503, "Unavailable", headers, io.BytesIO(b""))
            return _mock_urlopen(FAKE_DATA)

        with patch("urllib.request.urlopen", side_effect=flaky), patch("time.sleep") as mock_sleep:
            version, _, _ = fetch_pypi_info("requests", retries=2)

        assert version == "2.0.0"
        mock_sleep.assert_called_once_with(7.0)


class TestUploadDate:
    def test_known_version(self) -> None:
        assert _upload_date(FAKE_RELEASES, "1.0.0") == "2022-01-01"

    def test_unknown_version(self) -> None:
        assert _upload_date(FAKE_RELEASES, "9.9.9") is None

    def test_none_version(self) -> None:
        assert _upload_date(FAKE_RELEASES, None) is None

    def test_empty_files(self) -> None:
        assert _upload_date({"1.0.0": []}, "1.0.0") is None


class TestMaturity:
    def test_mature_release(self) -> None:
        files = [{"upload_time": "2024-01-01T00:00:00"}]
        assert _is_mature(files, 7, today=date(2024, 1, 8)) is True

    def test_recent_release(self) -> None:
        files = [{"upload_time": "2024-01-01T00:00:00"}]
        assert _is_mature(files, 8, today=date(2024, 1, 8)) is False


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [
        (">=3.10", "3.10"),
        (">=3.10,<4", "3.10"),
        ("~=3.11", "3.11"),
        ("==3.12.*", "3.12"),
        ("<4", None),
        ("", None),
        (None, None),
        ("not a spec", None),
    ],
)
def test_minimum_python(requirement, expected) -> None:
    assert minimum_python(requirement) == (Version(expected) if expected else None)
