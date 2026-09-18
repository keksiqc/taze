from __future__ import annotations

import pytest

from taze.config import ConfigError, load_config, package_mode_for


class TestLoadConfig:
    def test_loads_taze_toml(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        (tmp_path / "taze.toml").write_text('include = "httpx"\nconcurrency = 4\noutput_json = true\nunknown = true\n')
        assert load_config(tmp_path) == {"include": "httpx", "concurrency": 4, "output_json": True}

    def test_loads_tool_table_from_pyproject(self, tmp_path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'demo'\n[tool.taze]\nignore_paths = ['examples/**']\ninclude_locked = true\n",
        )
        assert load_config(tmp_path) == {"ignore_paths": ["examples/**"], "include_locked": True}

    def test_prefers_taze_toml_over_pyproject(self, tmp_path) -> None:
        (tmp_path / "taze.toml").write_text('exclude = "pytest"\n')
        (tmp_path / "pyproject.toml").write_text("[tool.taze]\nexclude = 'ruff'\n")
        assert load_config(tmp_path) == {"exclude": "pytest"}

    def test_prefers_environment_over_toml(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "taze.toml").write_text('include = "toml"\n')
        monkeypatch.setenv("TAZE_INCLUDE", "env")
        assert load_config(tmp_path)["include"] == "env"

    def test_rejects_malformed_taze_toml(self, tmp_path) -> None:
        (tmp_path / "taze.toml").write_text("concurrency = = 3\n")
        with pytest.raises(ConfigError, match="taze.toml"):
            load_config(tmp_path)

    def test_rejects_malformed_pyproject(self, tmp_path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.taze\n")
        with pytest.raises(ConfigError, match="pyproject.toml"):
            load_config(tmp_path)

    def test_rejects_wrong_value_type(self, tmp_path) -> None:
        (tmp_path / "taze.toml").write_text('concurrency = "many"\n')
        with pytest.raises(ConfigError, match="concurrency"):
            load_config(tmp_path)

    def test_rejects_invalid_environment_value(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("TAZE_CONCURRENCY", "abc")
        with pytest.raises(ConfigError, match="TAZE_CONCURRENCY"):
            load_config(tmp_path)

    def test_rejects_missing_explicit_config_file(self, tmp_path) -> None:
        with pytest.raises(ConfigError, match="no such file"):
            load_config(tmp_path, tmp_path / "missing.toml")


class TestPackageMode:
    def test_matches_exact_name(self) -> None:
        assert package_mode_for("requests", {"requests": "patch"}) == "patch"

    def test_matches_regular_expression(self) -> None:
        assert package_mode_for("django-rest-framework", {"/django-.*/": "minor"}) == "minor"

    def test_can_ignore_package(self) -> None:
        assert package_mode_for("setuptools", {"setuptools": "ignore"}) == "ignore"

    def test_rejects_unknown_mode(self) -> None:
        assert package_mode_for("requests", {"requests": "invalid"}) is None
