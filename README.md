# taze 🥬

Keep your Python dependencies fresh.

Inspired by [taze](https://github.com/antfu-collective/taze) for Node.js — ported to the Python ecosystem with support for `pyproject.toml` and `requirements.txt`.

---

## Features

- Checks all dependencies against PyPI in parallel
- Shows the bump level (patch / minor / **MAJOR**) with colors
- Displays release age for both the current and latest version
- Supports `pyproject.toml` (PEP 508, PEP 735, uv) and `requirements*.txt`
- Writes updated version constraints back to the file (`-w`)
- Runs `uv sync` after writing (`-i`)
- Interactive package selection (`-I`)
- Recursive monorepo scanning (`-r`)
- Pre-release support (`newest` / `next` mode)
- Regex filtering for include / exclude

---

## Installation

```sh
uv tool install taze
# or
uvx taze
# or
pip install taze
```

---

## Usage

```
taze [mode] [options]
```

### Modes

| Mode      | What it shows                          |
|-----------|----------------------------------------|
| `default` | All stable updates (default)           |
| `major`   | Same as default                        |
| `minor`   | Minor and patch updates only           |
| `patch`   | Patch updates only                     |
| `latest`  | All stable updates                     |
| `stable`  | All stable updates                     |
| `newest`  | All updates including pre-releases     |
| `next`    | Same as newest                         |

### Examples

```sh
# Check everything in the current directory
taze

# Only show minor and patch updates
taze minor

# Write patch updates back to pyproject.toml
taze patch -w

# Write all updates and run uv sync
taze -w -i

# Interactive — pick which packages to update
taze -I

# Include pre-releases
taze newest

# Scan all subdirectories (monorepo)
taze -r

# Only check specific packages
taze -n requests,httpx

# Skip packages matching a pattern
taze -x /^pytest/

# Sort by largest update first
taze --sort diff-desc

# Machine-readable JSON output
taze --json
```

### Options

```
  -w, --write              Write updates back to file
  -i, --install            Run uv sync after writing (implies -w)
  -u, --update             Alias for --install
  -I, --interactive        Choose which packages to update interactively
  -r, --recursive          Scan subdirectories for pyproject.toml / requirements*.txt
  -a, --all                Show up-to-date packages too
  -n, --include <deps>     Only check these packages (comma-separated or /regex/)
  -x, --exclude <deps>     Skip these packages (comma-separated or /regex/)
  -C, --cwd <path>         Working directory
  -s, --silent             No output
  -v, --version            Show version
      --sort <type>        Sort output: name-asc | name-desc | diff-asc | diff-desc
      --fail-on-outdated   Exit with code 1 if any outdated dependencies are found
      --concurrency <n>    Number of concurrent PyPI requests (default: 10)
```

---

## Output

```
  📦  pyproject.toml  /home/user/myproject/pyproject.toml

  dependencies  3 outdated
    packaging  ~8mo  >=25.2     →  >=26.2     ~3mo  MAJOR
    rich       ~4mo  >=14.0.0   →  >=15.0.0   ~3d   MAJOR
    typer      ~6mo  >=0.25.7   →  >=0.26.7   ~3d   minor

  group:dev  1 outdated
    ruff       ~8mo  >=0.14.1   →  >=0.15.19  ~1d   minor

  Run taze -w to write 4 update(s) to pyproject.toml
```

The age columns show how old the **current** pinned version is and how recently the **latest** version was released — green for < 4 weeks, yellow for < 6 months, red for older.

---

## Supported file formats

| File                    | Section                            |
|-------------------------|------------------------------------|
| `pyproject.toml`        | `[project] dependencies`           |
| `pyproject.toml`        | `[project.optional-dependencies.*]`|
| `pyproject.toml`        | `[dependency-groups.*]` (PEP 735)  |
| `pyproject.toml`        | `[tool.uv.dev-dependencies]`       |
| `requirements*.txt`     | Standard pip format                |

---

## License

MIT
