from __future__ import annotations

import re
import tomllib
from typing import TYPE_CHECKING

from packaging.requirements import InvalidRequirement, Requirement

from taze.models import DepInfo, FileKind


if TYPE_CHECKING:
    from pathlib import Path


def parse_dep_string(
    raw: str,
    *,
    source_file: Path | None = None,
    file_kind: FileKind = FileKind.PYPROJECT,
    line_number: int | None = None,
) -> DepInfo | None:
    """Parse a raw dependency string into a DepInfo, or None if it should be skipped."""
    raw = raw.strip()
    if not raw or raw.startswith(("#", "-")):
        return None
    raw = re.sub(r"\s+#.*$", "", raw).strip()
    if not raw:
        return None

    try:
        req = Requirement(raw)
    except InvalidRequirement, Exception:
        return None

    name = req.name.lower().replace("_", "-")
    specs = list(req.specifier)
    current: str | None = None
    operator: str | None = None

    for op in ("===", "==", ">=", "~=", ">"):
        for spec in specs:
            if spec.operator == op:
                current = spec.version
                operator = op
                break
        if current:
            break

    return DepInfo(
        raw=raw,
        name=name,
        current=current,
        operator=operator,
        source_file=source_file,
        file_kind=file_kind,
        line_number=line_number,
    )


def parse_pyproject(path: Path) -> dict[str, list[str]]:
    """Return group_label → raw dep strings from all recognised sections."""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    groups: dict[str, list[str]] = {}

    project_deps: list = data.get("project", {}).get("dependencies", [])
    if project_deps:
        groups["dependencies"] = [d for d in project_deps if isinstance(d, str)]

    for grp, dep_list in data.get("project", {}).get("optional-dependencies", {}).items():
        groups[f"optional:{grp}"] = [d for d in dep_list if isinstance(d, str)]

    for grp, dep_list in data.get("dependency-groups", {}).items():
        str_deps = [d for d in dep_list if isinstance(d, str)]
        if str_deps:
            groups[f"group:{grp}"] = str_deps

    uv_dev: list = data.get("tool", {}).get("uv", {}).get("dev-dependencies", [])
    if uv_dev:
        groups["dev-dependencies"] = [d for d in uv_dev if isinstance(d, str)]

    pdm_dev = data.get("tool", {}).get("pdm", {}).get("dev-dependencies", {})
    if isinstance(pdm_dev, dict):
        for name, dep_list in pdm_dev.items():
            if isinstance(name, str) and isinstance(dep_list, list):
                deps = [d for d in dep_list if isinstance(d, str)]
                if deps:
                    groups[f"pdm:{name}"] = deps

    hatch_envs = data.get("tool", {}).get("hatch", {}).get("envs", {})
    if isinstance(hatch_envs, dict):
        for name, settings in hatch_envs.items():
            if isinstance(name, str) and isinstance(settings, dict):
                deps = [d for d in settings.get("dependencies", []) if isinstance(d, str)]
                if deps:
                    groups[f"hatch:{name}"] = deps

    return groups


def parse_project_name(path: Path) -> str | None:
    """Return a normalised PEP 621 project name, when one is declared."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    name = data.get("project", {}).get("name")
    if not isinstance(name, str) or not name:
        return None
    return name.lower().replace("_", "-")


def parse_requirements_file(path: Path) -> list[tuple[int, str]]:
    """Return (line_number, dep_string) pairs from a requirements file."""
    result: list[tuple[int, str]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-")):
            continue
        dep = re.sub(r"\s+#.*$", "", stripped).strip()
        if dep:
            result.append((i, dep))
    return result


def build_name_filter(pattern: str) -> re.Pattern[str] | None:
    """
    Build a compiled regex from a comma-separated list.

    Entries wrapped in /slashes/ are treated as raw regex patterns;
    plain names are matched literally (normalised to lowercase with hyphens).
    """
    parts = [p.strip() for p in pattern.split(",") if p.strip()]
    if not parts:
        return None
    alternatives: list[str] = []
    for p in parts:
        if p.startswith("/") and p.endswith("/") and len(p) > 2:
            alternatives.append(p[1:-1])
        else:
            alternatives.append(re.escape(p.lower().replace("_", "-")))
    return re.compile(r"^(?:" + "|".join(alternatives) + r")$")
