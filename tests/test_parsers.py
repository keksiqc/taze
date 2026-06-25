from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

from taze.parsers import (
    build_name_filter,
    parse_dep_string,
    parse_pyproject,
    parse_requirements_file,
)


if TYPE_CHECKING:
    from pathlib import Path


class TestParseDepString:
    def test_eq_pin(self) -> None:
        d = parse_dep_string("requests==2.28.0")
        assert d is not None
        assert d.name == "requests"
        assert d.current == "2.28.0"
        assert d.operator == "=="

    def test_gte_pin(self) -> None:
        d = parse_dep_string("httpx>=0.24.0")
        assert d is not None
        assert d.current == "0.24.0"
        assert d.operator == ">="

    def test_compat_pin(self) -> None:
        d = parse_dep_string("rich~=13.0")
        assert d is not None
        assert d.operator == "~="

    def test_no_version(self) -> None:
        d = parse_dep_string("requests")
        assert d is not None
        assert d.current is None
        assert d.operator is None

    def test_normalises_name(self) -> None:
        d = parse_dep_string("My_Package==1.0")
        assert d is not None
        assert d.name == "my-package"

    def test_strips_inline_comment(self) -> None:
        d = parse_dep_string("requests==2.28.0  # pinned for compat")
        assert d is not None
        assert d.current == "2.28.0"

    def test_skips_comment_lines(self) -> None:
        assert parse_dep_string("# this is a comment") is None

    def test_skips_empty(self) -> None:
        assert parse_dep_string("   ") is None

    def test_skips_dash_flags(self) -> None:
        assert parse_dep_string("-r base.txt") is None

    def test_extras(self) -> None:
        d = parse_dep_string("uvicorn[standard]>=0.20.0")
        assert d is not None
        assert d.name == "uvicorn"
        assert d.current == "0.20.0"


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

    def test_empty_project(self, tmp_path) -> None:
        p = self._write(tmp_path, "[project]\nname = 'foo'\n")
        assert parse_pyproject(p) == {}


class TestParseRequirementsFile:
    def _write(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "requirements.txt"
        p.write_text(textwrap.dedent(content))
        return p

    def test_basic(self, tmp_path) -> None:
        p = self._write(tmp_path, "requests==2.28.0\nhttpx>=0.24\n")
        pairs = parse_requirements_file(p)
        assert len(pairs) == 2
        assert pairs[0] == (1, "requests==2.28.0")
        assert pairs[1] == (2, "httpx>=0.24")

    def test_skips_comments_and_blank(self, tmp_path) -> None:
        p = self._write(tmp_path, "# header\n\nrequests==2.28.0\n")
        pairs = parse_requirements_file(p)
        assert len(pairs) == 1
        assert pairs[0][0] == 3

    def test_skips_dash_flags(self, tmp_path) -> None:
        p = self._write(tmp_path, "-r base.txt\nrequests==2.28.0\n")
        pairs = parse_requirements_file(p)
        assert len(pairs) == 1

    def test_strips_inline_comment(self, tmp_path) -> None:
        p = self._write(tmp_path, "requests==2.28.0  # pinned\n")
        pairs = parse_requirements_file(p)
        assert pairs[0][1] == "requests==2.28.0"


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

    def test_underscore_normalised(self) -> None:
        pat = build_name_filter("my_package")
        assert pat is not None
        assert pat.match("my-package")

    def test_empty_returns_none(self) -> None:
        assert build_name_filter("") is None
        assert build_name_filter("  ,  ") is None
