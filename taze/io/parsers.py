"""Parse supported Python dependency file formats."""

from __future__ import annotations

import re
import tomllib
from typing import TYPE_CHECKING

from packaging.markers import UndefinedEnvironmentName
from packaging.requirements import InvalidRequirement, Requirement

from taze.models import DepInfo


if TYPE_CHECKING:
    from pathlib import Path


def parse_dep_string(
    raw: str,
    *,
    line_number: int | None = None,
    source: str = "dependencies",
    toml_section: str | None = None,
    toml_key: str | None = None,
    toml_value: str | None = None,
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
    except InvalidRequirement:
        return None

    if req.url:
        return None
    if req.marker and "extra" not in str(req.marker):
        try:
            if not req.marker.evaluate():
                return None
        except UndefinedEnvironmentName:
            pass

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
        line_number=line_number,
        source=source,
        toml_section=toml_section,
        toml_key=toml_key,
        toml_value=toml_value,
    )


def _as_strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _poetry_requirement(name: str, value: object) -> tuple[str, str] | None:
    """Turn a simple Poetry constraint into a PEP 508 requirement."""
    if isinstance(value, dict):
        value = value.get("version")
    if not isinstance(value, str) or not value or value in {"*", "latest"}:
        return None
    constraint = value.strip()
    if constraint.startswith("^"):
        lower = constraint[1:]
        try:
            version = lower.split(".")
            major, minor = int(version[0]), int(version[1]) if len(version) > 1 else 0
            if major:
                upper = f"{major + 1}.0"
            elif minor:
                upper = f"0.{minor + 1}"
            else:
                upper = f"0.0.{int(version[2]) + 1 if len(version) > 2 else 1}"
            constraint = f">={lower},<{upper}"
        except IndexError, ValueError:
            return None
    elif constraint.startswith("~") and not constraint.startswith("~="):
        lower = constraint[1:]
        parts = lower.split(".")
        try:
            if len(parts) == 1:
                upper = f"{int(parts[0]) + 1}.0"
            else:
                upper = f"{int(parts[0])}.{int(parts[1]) + 1}"
            constraint = f">={lower},<{upper}"
        except IndexError, ValueError:
            return None
    elif re.fullmatch(r"v?\d+(?:\.\d+){0,2}", constraint):
        constraint = f"=={constraint.lstrip('v')}"
    return f"{name}{constraint}", value


def parse_pyproject_entries(path: Path) -> dict[str, list[tuple[str, dict[str, str]]]]:
    """Return dependency groups with raw requirements and optional write metadata."""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    groups: dict[str, list[tuple[str, dict[str, str]]]] = {}
    project = data.get("project", {})
    if isinstance(project, dict):
        deps = _as_strings(project.get("dependencies"))
        if deps:
            groups["dependencies"] = [(dep, {}) for dep in deps]
        optional = project.get("optional-dependencies", {})
        if isinstance(optional, dict):
            for name, values in optional.items():
                deps = _as_strings(values)
                if isinstance(name, str) and deps:
                    groups[f"optional:{name}"] = [(dep, {}) for dep in deps]

    dependency_groups = data.get("dependency-groups", {})
    if isinstance(dependency_groups, dict):
        for name, values in dependency_groups.items():
            deps = _as_strings(values)
            if isinstance(name, str) and deps:
                groups[f"group:{name}"] = [(dep, {}) for dep in deps]

    tool = data.get("tool", {})
    if not isinstance(tool, dict):
        return groups

    uv = tool.get("uv", {})
    if isinstance(uv, dict):
        for key, label in (
            ("dev-dependencies", "dev-dependencies"),
            ("constraint-dependencies", "constraints"),
            ("override-dependencies", "overrides"),
        ):
            deps = _as_strings(uv.get(key))
            if deps:
                groups[label] = [(dep, {}) for dep in deps]

    pdm_dev = tool.get("pdm", {}).get("dev-dependencies", {}) if isinstance(tool.get("pdm"), dict) else {}
    if isinstance(pdm_dev, dict):
        for name, values in pdm_dev.items():
            deps = _as_strings(values)
            if isinstance(name, str) and deps:
                groups[f"pdm:{name}"] = [(dep, {}) for dep in deps]

    poetry = tool.get("poetry", {})
    if isinstance(poetry, dict):
        poetry_groups: list[tuple[str, object]] = [("poetry", poetry.get("dependencies", {}))]
        dev = poetry.get("dev-dependencies", {})
        poetry_groups.append(("poetry:dev", dev))
        named = poetry.get("group", {})
        if isinstance(named, dict):
            poetry_groups.extend(
                (f"poetry:{name}", settings.get("dependencies", {}))
                for name, settings in named.items()
                if isinstance(settings, dict)
            )
        for label, values in poetry_groups:
            if not isinstance(values, dict):
                continue
            entries: list[tuple[str, dict[str, str]]] = []
            for name, value in values.items():
                if not isinstance(name, str) or name == "python":
                    continue
                converted = _poetry_requirement(name, value)
                if converted:
                    raw, original = converted
                    entries.append(
                        (
                            raw,
                            {
                                "toml_section": _poetry_section(label),
                                "toml_key": name,
                                "toml_value": original,
                            },
                        ),
                    )
            if entries:
                groups[label] = entries

    hatch = tool.get("hatch", {})
    envs = hatch.get("envs", {}) if isinstance(hatch, dict) else {}
    if isinstance(envs, dict):
        for name, settings in envs.items():
            if isinstance(name, str) and isinstance(settings, dict):
                deps = _as_strings(settings.get("dependencies"))
                if deps:
                    groups[f"hatch:{name}"] = [(dep, {}) for dep in deps]

    return groups


def _poetry_section(label: str) -> str:
    if label == "poetry":
        return "tool.poetry.dependencies"
    if label == "poetry:dev":
        return "tool.poetry.dev-dependencies"
    return f"tool.poetry.group.{label.split(':', 1)[1]}.dependencies"


def parse_pyproject(path: Path) -> dict[str, list[str]]:
    """Return group_label → raw dep strings from all recognised sections."""
    return {label: [raw for raw, _metadata in entries] for label, entries in parse_pyproject_entries(path).items()}


def parse_project_name(path: Path) -> str | None:
    """Return a normalised PEP 621 project name, when one is declared."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    project = data.get("project", {})
    name = project.get("name") if isinstance(project, dict) else None
    if not isinstance(name, str) or not name:
        poetry = data.get("tool", {}).get("poetry", {})
        name = poetry.get("name") if isinstance(poetry, dict) else None
    if not isinstance(name, str) or not name:
        return None
    return name.lower().replace("_", "-")


def build_name_filter(pattern: str) -> re.Pattern[str] | None:
    """
    Build a compiled regex from a comma-separated list.

    Entries wrapped in /slashes/ are treated as raw regex patterns;
    plain names are matched literally (normalised to lowercase with hyphens).
    ``*`` is a small glob convenience supported by the upstream CLI.
    """
    parts = [p.strip() for p in pattern.split(",") if p.strip()]
    if not parts:
        return None
    alternatives: list[str] = []
    flags = 0
    for part in parts:
        if part.startswith("/") and "/" in part[1:]:
            end = part.rfind("/")
            suffix = part[end + 1 :]
            if re.fullmatch(r"[aiLmsux-]*", suffix):
                alternatives.append(f"(?=.*(?:{part[1:end]}))")
                if "i" in suffix:
                    flags |= re.IGNORECASE
                continue
        normalised = part.lower().replace("_", "-")
        escaped = re.escape(normalised).replace(r"\*", ".*?")
        alternatives.append(f"(?=^{escaped}$)")
    return re.compile(r"^(?:" + "|".join(alternatives) + r").*$", flags)


def _selector_parts(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (str, list, tuple)):
        return []
    values = [value] if isinstance(value, str) else list(value)
    return [part.strip() for value in values if isinstance(value, str) for part in value.split(",") if part.strip()]


def parse_selectors(
    value: str | list[str] | tuple[str, ...] | None,
) -> tuple[re.Pattern[str] | None, list[tuple[re.Pattern[str], tuple[str, ...]]]]:
    """Split name filters from ``name@version`` selectors."""
    names: list[str] = []
    ranged: list[tuple[re.Pattern[str], tuple[str, ...]]] = []
    for selector in _selector_parts(value):
        if selector.startswith("/") and selector.endswith("/"):
            names.append(selector)
            continue
        at = selector.rfind("@")
        if at > 0:
            name, raw_ranges = selector[:at], selector[at + 1 :]
            pattern = build_name_filter(name)
            if pattern:
                ranged.append((pattern, tuple(item.strip() for item in raw_ranges.split("||") if item.strip())))
        else:
            names.append(selector)
    return build_name_filter(",".join(names)), ranged


def selector_ranges(
    name: str,
    selectors: list[tuple[re.Pattern[str], tuple[str, ...]]],
) -> tuple[str, ...]:
    """Return version selectors applying to a normalised package name."""
    return tuple(version for pattern, ranges in selectors if pattern.fullmatch(name) for version in ranges)
