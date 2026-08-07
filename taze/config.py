"""Loading, validating, and merging project-local taze configuration."""

from __future__ import annotations

import fnmatch
import re
import tomllib
import types
import typing
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from taze.models import MODES


if TYPE_CHECKING:
    import typer


class ConfigError(Exception):
    """Raised when ``taze.toml`` / ``[tool.taze]`` has an invalid key or value."""


@dataclass(frozen=True, slots=True)
class TazeConfig:
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
    package_mode: dict[str, str] = field(default_factory=dict)
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


# TOML keys that don't map 1:1 onto a TazeConfig field name once dashes become
# underscores (either because the flag name is shorter than the field, or the
# field name had to dodge a Python builtin).
_KEY_ALIASES = {
    "json": "output_json",
    "all": "all_deps",
    "retry": "retries",
}

_FIELD_TYPES = typing.get_type_hints(TazeConfig)


def load_config(root: Path, config_path: Path | None = None) -> dict[str, Any]:
    """Load ``taze.toml`` or ``[tool.taze]``, validated against :class:`TazeConfig`.

    Unknown keys are ignored (so older/newer taze versions can share a config
    file); recognized keys are type-checked against the dataclass field they
    map to and raise :class:`ConfigError` on a mismatch.
    """
    path = config_path or root / "taze.toml"
    try:
        if path.is_file():
            with path.open("rb") as f:
                data = tomllib.load(f)
            if isinstance(data.get("tool"), dict) and isinstance(data["tool"].get("taze"), dict):
                data = data["tool"]["taze"]
            else:
                data = data.get("taze", data)
        else:
            pyproject = root / "pyproject.toml"
            if not pyproject.is_file():
                return {}
            with pyproject.open("rb") as f:
                parsed = tomllib.load(f)
            tool = parsed.get("tool", {})
            data = tool.get("taze", {}) if isinstance(tool, dict) else {}
    except OSError, tomllib.TOMLDecodeError:
        return {}

    if not isinstance(data, dict):
        return {}

    config: dict[str, Any] = {}
    for key, value in data.items():
        normalized = key.replace("-", "_")
        normalized = _KEY_ALIASES.get(normalized, normalized)
        if normalized not in _FIELD_TYPES:
            continue
        _validate_value(normalized, value, _FIELD_TYPES[normalized])
        config[normalized] = value
    return config


def resolve_config(context: typer.Context, cli: TazeConfig, overrides: dict[str, Any]) -> TazeConfig:
    """Layer project-file settings under explicit CLI flags: CLI flag > config file > CLI default."""
    changes: dict[str, Any] = {}
    for config_field in fields(cli):
        if config_field.name not in overrides:
            continue
        try:
            source = context.get_parameter_source(config_field.name)
        except Exception:  # noqa: BLE001 - defensive: never let config resolution crash on this
            source = None
        if source is None or source.name == "DEFAULT":
            changes[config_field.name] = overrides[config_field.name]
    return replace(cli, **changes) if changes else cli


def _acceptable_types(annotation: object) -> tuple[type, ...]:
    """Flatten a field's type hint (unions, generics) into concrete isinstance-able types."""
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        result: list[type] = []
        for arg in typing.get_args(annotation):
            result.extend(_acceptable_types(arg))
        return tuple(result)
    if annotation is type(None):
        return (type(None),)
    if origin is not None:
        return (origin,)
    return (typing.cast("type", annotation),)


def _type_names(types_: tuple[type, ...]) -> str:
    names = ["null" if t is type(None) else t.__name__ for t in types_]
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"


def _validate_value(name: str, value: object, annotation: object) -> None:
    acceptable = _acceptable_types(annotation)
    # bool is an int subclass, but "concurrency = true" is a typo, not a 1/0.
    if bool not in acceptable and isinstance(value, bool):
        raise ConfigError(f"{name!r} must be {_type_names(acceptable)}, got bool")
    if not isinstance(value, acceptable):
        raise ConfigError(f"{name!r} must be {_type_names(acceptable)}, got {type(value).__name__}")


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
