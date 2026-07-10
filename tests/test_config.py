from __future__ import annotations

from taze.config import load_config, package_mode_for


class TestLoadConfig:
    def test_loads_taze_toml(self, tmp_path) -> None:
        (tmp_path / "taze.toml").write_text('include = "httpx"\nconcurrency = 4\nunknown = true\n')
        assert load_config(tmp_path) == {"include": "httpx", "concurrency": 4}

    def test_loads_tool_table_from_pyproject(self, tmp_path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'demo'\n[tool.taze]\nignore-paths = ['examples/**']\ninclude-locked = true\n",
        )
        assert load_config(tmp_path) == {"ignore_paths": ["examples/**"], "include_locked": True}

    def test_prefers_taze_toml_over_pyproject(self, tmp_path) -> None:
        (tmp_path / "taze.toml").write_text('exclude = "pytest"\n')
        (tmp_path / "pyproject.toml").write_text("[tool.taze]\nexclude = 'ruff'\n")
        assert load_config(tmp_path) == {"exclude": "pytest"}


class TestPackageMode:
    def test_matches_exact_name(self) -> None:
        assert package_mode_for("requests", {"requests": "patch"}) == "patch"

    def test_matches_regular_expression(self) -> None:
        assert package_mode_for("django-rest-framework", {"/django-.*/": "minor"}) == "minor"

    def test_can_ignore_package(self) -> None:
        assert package_mode_for("setuptools", {"setuptools": "ignore"}) == "ignore"

    def test_rejects_unknown_mode(self) -> None:
        assert package_mode_for("requests", {"requests": "invalid"}) is None
