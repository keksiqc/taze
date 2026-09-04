"""Loading, validating, and merging project-local taze configuration."""

from __future__ import annotations

import fnmatch
import os
import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import msgspec

from taze.models import MODES


if TYPE_CHECKING:
    import typer


ConfigError = msgspec.ValidationError


class TazeConfig(msgspec.Struct, frozen=True):
    """Every option that can be set from ``taze.toml`` / ``[tool.taze]``.

    Field names must match the corresponding ``main()`` parameter name exactly —
    that's what :func:`resolve_config` uses to look up whether the CLI flag was
    passed explicitly, so a mismatch would silently make the setting inert.
    """

    mode: str = "default"
    include: str | None = None
    exclude: str | None = None
    recursive: bool = False
    ignore_paths: str | list[str] | None = None
    ignore_other_workspaces: bool = True
    interactive: bool = False
    all_deps: bool = False
    group: bool = True
    include_locked: bool = False
    maturity_period: int = 0
    maturity_period_exclude: str | None = None
    package_mode: dict[str, str] = msgspec.field(default_factory=dict)
    sort: str | None = "diff-asc"
    fail_on_outdated: bool = False
    silent: bool = False
    output_json: bool = False
    github_actions: bool = True
    github_actions_style: str = "auto"
    github_actions_pin: bool = False
    concurrency: int = 10
    request_timeout: float = 10.0
    retries: int = 2
    write: bool = False
    install: bool = False
    update: bool = False
    force: bool = False


_FIELD_TYPES: dict[str, Any] = {field.name: field.type for field in msgspec.structs.fields(TazeConfig)}


def load_config(root: Path, config_path: Path | None = None) -> dict[str, Any]:
    """Load ``taze.toml`` or ``[tool.taze]``, validated against :class:`TazeConfig`.

    Unknown keys are ignored (so older/newer taze versions can share a config
    file); recognized keys are validated by :class:`TazeConfig`. Precedence,
    highest first: ``TAZE_*`` environment variables, ``taze.toml`` (or
    ``--config``), then ``[tool.taze]`` in ``pyproject.toml``.
    """
    toml_file = config_path or root / "taze.toml"
    pyproject_file = root / "pyproject.toml"
    try:
        toml_table_header = _toml_table_header(toml_file)
        merged: dict[str, Any] = {}
        merged.update(_read_toml_table(pyproject_file, ("tool", "taze")))
        merged.update(_read_toml_table(toml_file, toml_table_header))
        merged.update(_read_env())
    except (OSError, tomllib.TOMLDecodeError):
        return {}

    return {name: msgspec.convert(value, type=_FIELD_TYPES[name], strict=False) for name, value in merged.items()}


def _read_toml_table(path: Path, header: tuple[str, ...]) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as file:
        data = tomllib.load(file)
    table: Any = data
    for key in header:
        table = table.get(key, {}) if isinstance(table, dict) else {}
    return {name: value for name, value in table.items() if name in _FIELD_TYPES} if isinstance(table, dict) else {}


def _read_env() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, annotation in _FIELD_TYPES.items():
        raw = os.environ.get(f"TAZE_{name.upper()}")
        if raw is not None:
            result[name] = _coerce_env_value(raw, annotation)
    return result


def _coerce_env_value(raw: str, annotation: Any) -> Any:
    """Coerce a raw environment string toward one of a field's accepted types."""
    if annotation is bool:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if annotation in (int, float, str):
        return annotation(raw)
    for member in getattr(annotation, "__args__", ()):
        if member is type(None):
            continue
        try:
            return _coerce_env_value(raw, member)
        except (TypeError, ValueError):
            continue
    return raw


def _toml_table_header(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    with path.open("rb") as file:
        data = tomllib.load(file)
    tool = data.get("tool")
    if isinstance(tool, dict) and isinstance(tool.get("taze"), dict):
        return ("tool", "taze")
    return ("taze",) if isinstance(data.get("taze"), dict) else ()


def resolve_config(context: typer.Context, cli: TazeConfig, overrides: dict[str, Any]) -> TazeConfig:
    """Layer project-file settings under explicit CLI flags: CLI flag > config file > CLI default."""
    changes: dict[str, Any] = {}
    for name, value in overrides.items():
        try:
            source = context.get_parameter_source(name)
        except Exception:  # noqa: BLE001 - defensive: never let config resolution crash on this
            source = None
        if source is None or source.name == "DEFAULT":
            changes[name] = value
    return msgspec.structs.replace(cli, **changes) if changes else cli


def package_mode_for(name: str, package_modes: object) -> str | None:
    """Return an exact or slash-delimited-regex policy for a package name."""
    if not isinstance(package_modes, dict):
        return None
    for pattern, mode in package_modes.items():
        if not isinstance(pattern, str) or not isinstance(mode, str):
            continue
        if mode != "ignore" and mode not in MODES:
            continue
        normalized_pattern = pattern.lower().replace("_", "-")
        if normalized_pattern == name:
            return mode
        if pattern.startswith("/") and pattern.endswith("/"):
            try:
                if re.search(pattern[1:-1], name):
                    return mode
            except re.error:
                continue
        if fnmatch.fnmatchcase(name, pattern.lower().replace("_", "-")):
            return mode
    return None
