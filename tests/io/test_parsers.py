from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

from taze.io.parsers import (
    build_name_filter,
    parse_dep_string,
    parse_project_name,
    parse_pyproject,
    parse_pyproject_entries,
    parse_selectors,
    selector_ranges,
)


if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("raw", "name", "current", "operator"),
    [
        ("requests==2.28.0", "requests", "2.28.0", "=="),
        ("requests===2.28.0", "requests", "2.28.0", "==="),
        ("httpx>=0.24.0", "httpx", "0.24.0", ">="),
        ("rich~=13.0", "rich", "13.0", "~="),
        ("requests", "requests", None, None),
        ("My_Package==1.0", "my-package", "1.0", "=="),
        ("requests==2.28.0  # pinned for compat", "requests", "2.28.0", "=="),
        ("uvicorn[standard]>=0.20.0", "uvicorn", "0.20.0", ">="),
    ],
)
def test_parse_dep_string(raw, name, current, operator) -> None:
    dep = parse_dep_string(raw)
    assert dep is not None
    assert (dep.name, dep.current, dep.operator) == (name, current, operator)


@pytest.mark.parametrize("raw", ["# this is a comment", "   ", "-r base.txt", "local @ file:///tmp/local"])
def test_parse_dep_string_skips_non_dependencies(raw) -> None:
    assert parse_dep_string(raw) is None


class TestParsePyproject:
    def _write(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "pyproject.toml"
        p.write_text(textwrap.dedent(content))
        return p

    def test_project_dependencies(self, tmp_path) -> None:
        p = self._write(
            tmp_path,
            """
            [project]
            dependencies = ["requests>=2.28", "rich>=13"]
            """,
        )
        groups = parse_pyproject(p)
        assert "dependencies" in groups
        assert groups["dependencies"] == ["requests>=2.28", "rich>=13"]

    def test_optional_dependencies(self, tmp_path) -> None:
        p = self._write(
            tmp_path,
            """
            [project.optional-dependencies]
            dev = ["pytest>=7"]
            """,
        )
        groups = parse_pyproject(p)
        assert "optional:dev" in groups
        assert groups["optional:dev"] == ["pytest>=7"]

    def test_dependency_groups(self, tmp_path) -> None:
        p = self._write(
            tmp_path,
            """
            [dependency-groups]
            test = ["pytest>=7", "coverage>=7"]
            """,
        )
        groups = parse_pyproject(p)
        assert "group:test" in groups

    def test_uv_dev_dependencies(self, tmp_path) -> None:
        p = self._write(
            tmp_path,
            """
            [tool.uv]
            dev-dependencies = ["ruff>=0.4"]
            """,
        )
        groups = parse_pyproject(p)
        assert "dev-dependencies" in groups

    def test_pdm_dev_dependencies(self, tmp_path) -> None:
        p = self._write(
            tmp_path,
            """
            [tool.pdm.dev-dependencies]
            test = ["pytest>=7"]
            """,
        )
        assert parse_pyproject(p)["pdm:test"] == ["pytest>=7"]

    def test_hatch_environment_dependencies(self, tmp_path) -> None:
        p = self._write(
            tmp_path,
            """
            [tool.hatch.envs.test]
            dependencies = ["pytest>=7"]
            """,
        )
        assert parse_pyproject(p)["hatch:test"] == ["pytest>=7"]

    def test_poetry_dependencies_are_converted(self, tmp_path) -> None:
        p = self._write(
            tmp_path,
            """
            [tool.poetry.dependencies]
            requests = "^2.0"
            """,
        )
        entries = parse_pyproject_entries(p)["poetry"]
        assert entries[0][0] == "requests>=2.0,<3.0"
        assert entries[0][1]["toml_value"] == "^2.0"

    def test_empty_project(self, tmp_path) -> None:
        p = self._write(tmp_path, "[project]\nname = 'foo'\n")
        assert parse_pyproject(p) == {}

    def test_project_name_is_normalised(self, tmp_path) -> None:
        p = self._write(tmp_path, "[project]\nname = 'My_Package'\n")
        assert parse_project_name(p) == "my-package"


class TestBuildNameFilter:
    def test_plain_name_matches(self) -> None:
        pat = build_name_filter("requests")
        assert pat is not None
        assert pat.match("requests")

    def test_plain_name_no_match(self) -> None:
        pat = build_name_filter("requests")
        assert pat is not None
        assert not pat.match("httpx")

    def test_multiple_names(self) -> None:
        pat = build_name_filter("requests,httpx")
        assert pat is not None
        assert pat.match("requests")
        assert pat.match("httpx")
        assert not pat.match("rich")

    def test_regex_pattern(self) -> None:
        # regex is anchored with ^(?:...)$ so the inner pattern must cover the full name
        pat = build_name_filter("/boto.*/")
        assert pat is not None
        assert pat.match("boto3")
        assert pat.match("botocore")
        assert not pat.match("requests")

    def test_regex_pattern_matches_substrings(self) -> None:
        pat = build_name_filter("/http/")
        assert pat is not None
        assert pat.match("httpx")
        assert not pat.match("requests")

    def test_underscore_normalised(self) -> None:
        pat = build_name_filter("my_package")
        assert pat is not None
        assert pat.match("my-package")

    def test_empty_returns_none(self) -> None:
        assert build_name_filter("") is None
        assert build_name_filter("  ,  ") is None

    def test_version_selector_does_not_exclude_whole_package(self) -> None:
        pattern, selectors = parse_selectors("requests@2")
        assert pattern is None
        assert selector_ranges("requests", selectors) == ("2",)
