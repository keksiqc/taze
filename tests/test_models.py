from __future__ import annotations

from typing import Any, cast

import pytest

from taze.models import DepInfo, bump_allowed, calc_bump


@pytest.mark.parametrize(
    ("current", "latest", "expected"),
    [
        ("1.0.0", "1.0.1", "patch"),
        ("1.0.0", "1.1.0", "minor"),
        ("1.0.0", "2.0.0", "major"),
        ("1.0.0", "1.0.0", "same"),
        ("2.0.0", "1.9.9", "same"),
        (None, "1.0.0", "?"),
        ("1.0.0", None, "?"),
        ("not-a-version", "1.0.0", "?"),
    ],
)
def test_calc_bump(current, latest, expected) -> None:
    assert calc_bump(current, latest) == expected


@pytest.mark.parametrize(
    ("bump", "mode", "expected"),
    [
        ("major", "default", True),
        ("major", "minor", False),
        ("major", "patch", False),
        ("minor", "minor", True),
        ("minor", "patch", False),
        ("patch", "default", True),
        ("patch", "major", True),
        ("patch", "minor", True),
        ("patch", "patch", True),
        ("patch", "newest", True),
        ("same", "default", False),
        ("?", "default", False),
    ],
)
def test_bump_allowed(bump, mode, expected) -> None:
    assert bump_allowed(bump, mode) is expected


class TestDepInfoProperties:
    def _make(self, **kw: Any) -> DepInfo:
        defaults = {"raw": "requests>=2.0.0", "name": "requests", "current": None, "operator": None}
        return DepInfo(**cast(dict[str, Any], {**defaults, **kw}))

    def test_current_spec_with_operator(self) -> None:
        d = self._make(operator=">=", current="2.0.0")
        assert d.current_spec == ">=2.0.0"

    def test_current_spec_no_pin(self) -> None:
        d = self._make(operator=None, current=None)
        assert d.current_spec == "(any)"

    @pytest.mark.parametrize(
        ("operator", "current", "latest", "expected"),
        [
            ("==", "1.0.0", "2.0.0", "==2.0.0"),
            ("~=", "1.2.3", "1.3.0", "~=1.3.0"),
            ("~=", "1.2", "1.3.0", "~=1.3"),
            (None, None, None, "—"),
        ],
    )
    def test_latest_spec(self, operator, current, latest, expected) -> None:
        assert self._make(operator=operator, current=current, latest=latest).latest_spec == expected

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"operator": ">=", "current": "1.0.0", "latest": "2.0.0", "bump": "major"}, True),
            ({"operator": ">=", "current": "1.0.0", "latest": "1.0.0", "bump": "same"}, False),
            ({"bump": "major", "fetch_error": True}, False),
        ],
    )
    def test_is_outdated(self, kwargs, expected) -> None:
        assert self._make(**kwargs).is_outdated is expected

    def test_is_shown_respects_mode(self) -> None:
        d = self._make(operator=">=", current="1.0.0", latest="2.0.0", bump="major")
        assert d.is_shown("default") is True
        assert d.is_shown("minor") is False
        assert d.is_shown("patch") is False

    def test_is_shown_prefers_package_mode(self) -> None:
        d = self._make(operator=">=", current="1.0.0", latest="2.0.0", bump="major", effective_mode="major")
        assert d.is_shown("patch") is True

    def test_is_locked_for_exact_pin(self) -> None:
        assert self._make(operator="==", current="1.0.0").is_locked is True
        assert self._make(operator=">=", current="1.0.0").is_locked is False

    @pytest.mark.parametrize("operator", ["==", ">="])
    def test_updated_raw(self, operator) -> None:
        d = self._make(raw=f"requests{operator}1.0.0", operator=operator, current="1.0.0", latest="2.0.0")
        assert d.updated_raw() == f"requests{operator}2.0.0"

    def test_updated_raw_preserves_markers_and_other_bounds(self) -> None:
        d = self._make(
            raw='requests>=1.0,<2.0; python_version < "3.13"',
            operator=">=",
            current="1.0",
            latest="1.9.2",
        )
        assert d.updated_raw() == 'requests>=1.9.2,<2.0; python_version < "3.13"'

    def test_updated_raw_preserves_extras(self) -> None:
        d = self._make(
            raw="uvicorn[standard]>=0.20.0,<1",
            operator=">=",
            current="0.20.0",
            latest="0.35.0",
        )
        assert d.updated_raw() == "uvicorn[standard]>=0.35.0,<1"

    def test_updated_raw_no_operator(self) -> None:
        d = self._make(raw="requests", operator=None, current=None, latest="2.0.0")
        assert d.updated_raw() == "requests"
