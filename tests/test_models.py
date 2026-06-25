from __future__ import annotations

from taze.models import DepInfo, bump_allowed, calc_bump


class TestCalcBump:
    def test_patch(self) -> None:
        assert calc_bump("1.0.0", "1.0.1") == "patch"

    def test_minor(self) -> None:
        assert calc_bump("1.0.0", "1.1.0") == "minor"

    def test_major(self) -> None:
        assert calc_bump("1.0.0", "2.0.0") == "major"

    def test_same(self) -> None:
        assert calc_bump("1.0.0", "1.0.0") == "same"

    def test_latest_older(self) -> None:
        assert calc_bump("2.0.0", "1.9.9") == "same"

    def test_missing_current(self) -> None:
        assert calc_bump(None, "1.0.0") == "?"

    def test_missing_latest(self) -> None:
        assert calc_bump("1.0.0", None) == "?"

    def test_invalid_version(self) -> None:
        assert calc_bump("not-a-version", "1.0.0") == "?"


class TestBumpAllowed:
    def test_major_in_default_mode(self) -> None:
        assert bump_allowed("major", "default") is True

    def test_major_blocked_in_minor_mode(self) -> None:
        assert bump_allowed("major", "minor") is False

    def test_major_blocked_in_patch_mode(self) -> None:
        assert bump_allowed("major", "patch") is False

    def test_minor_blocked_in_patch_mode(self) -> None:
        assert bump_allowed("minor", "patch") is False

    def test_minor_allowed_in_minor_mode(self) -> None:
        assert bump_allowed("minor", "minor") is True

    def test_patch_allowed_everywhere(self) -> None:
        for mode in ("default", "major", "minor", "patch", "newest"):
            assert bump_allowed("patch", mode) is True

    def test_same_never_allowed(self) -> None:
        assert bump_allowed("same", "default") is False

    def test_unknown_bump_never_allowed(self) -> None:
        assert bump_allowed("?", "default") is False


class TestDepInfoProperties:
    def _make(self, **kw) -> DepInfo:
        defaults = {"raw": "requests>=2.0.0", "name": "requests", "current": None, "operator": None}
        return DepInfo(**{**defaults, **kw})

    def test_current_spec_with_operator(self) -> None:
        d = self._make(operator=">=", current="2.0.0")
        assert d.current_spec == ">=2.0.0"

    def test_current_spec_no_pin(self) -> None:
        d = self._make(operator=None, current=None)
        assert d.current_spec == "(any)"

    def test_latest_spec_eq(self) -> None:
        d = self._make(operator="==", current="1.0.0", latest="2.0.0")
        assert d.latest_spec == "==2.0.0"

    def test_latest_spec_compat(self) -> None:
        # preserves same number of components as the pinned version
        d = self._make(operator="~=", current="1.2.3", latest="1.3.0")
        assert d.latest_spec == "~=1.3.0"

    def test_latest_spec_compat_two_part(self) -> None:
        d = self._make(operator="~=", current="1.2", latest="1.3.0")
        assert d.latest_spec == "~=1.3"

    def test_latest_spec_no_latest(self) -> None:
        d = self._make()
        assert d.latest_spec == "—"

    def test_is_outdated_true(self) -> None:
        d = self._make(operator=">=", current="1.0.0", latest="2.0.0", bump="major")
        assert d.is_outdated is True

    def test_is_outdated_false_when_same(self) -> None:
        d = self._make(operator=">=", current="1.0.0", latest="1.0.0", bump="same")
        assert d.is_outdated is False

    def test_is_outdated_false_on_fetch_error(self) -> None:
        d = self._make(bump="major", fetch_error=True)
        assert d.is_outdated is False

    def test_is_shown_respects_mode(self) -> None:
        d = self._make(operator=">=", current="1.0.0", latest="2.0.0", bump="major")
        assert d.is_shown("default") is True
        assert d.is_shown("minor") is False
        assert d.is_shown("patch") is False

    def test_updated_raw_eq(self) -> None:
        d = self._make(raw="requests==1.0.0", operator="==", current="1.0.0", latest="2.0.0")
        assert d.updated_raw() == "requests==2.0.0"

    def test_updated_raw_gte(self) -> None:
        d = self._make(raw="requests>=1.0.0", operator=">=", current="1.0.0", latest="2.0.0")
        assert d.updated_raw() == "requests>=2.0.0"

    def test_updated_raw_no_operator(self) -> None:
        d = self._make(raw="requests", operator=None, current=None, latest="2.0.0")
        assert d.updated_raw() == "requests"
