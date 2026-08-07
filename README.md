# taze 🥬

Keep your Python dependencies fresh.

Inspired by [taze](https://github.com/antfu-collective/taze), this is the Python-native version: the same safe-by-default CLI workflow, adapted for PyPI, PEP 440, Python project metadata, and GitHub Actions.

---

## Features

- Checks all dependencies against PyPI in parallel
- Shows the bump level (patch / minor / **MAJOR**) with colors
- Displays release age for both the current and latest version
- Supports `pyproject.toml` (PEP 508, PEP 735, uv, Poetry, PDM, and Hatch) and `requirements*.txt`
- Writes updated version constraints back to the file (`-w`)
- Detects uv, Poetry, PDM, and Pixi before installing (`-i`)
- Interactive package selection (`-I`)
- Recursive monorepo scanning (`-r`)
- Pre-release support (`newest` / `next` mode)
- Regex/glob filtering for include / exclude, including version selectors such as `requests@2`
- Checks GitHub Actions refs with tag or SHA-preserving writes
- JSON output for agents and CI, with optional fail-on-outdated status
- Safe PEP 440 range resolution, a local TTL cache, maturity periods, and per-package policies

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

```text
taze [mode] [options]
```

### Modes

| Mode      | What it shows                      |
| --------- | ---------------------------------- |
| `default` | Latest stable release allowed by the declared PEP 440 range |
| `major`   | Latest stable release, including major changes |
| `minor`   | Latest stable release in the current major version |
| `patch`   | Latest stable release in the current major/minor version |
| `latest`  | Alias for `major`                  |
| `stable`  | Range-safe stable updates           |
| `newest`  | All updates including pre-releases |
| `next`    | Same as newest                     |

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

# Interactive checkbox menu (↑/↓, space, a, → version, Enter, Esc)
taze -I

# Include pre-releases
taze newest

# Scan all subdirectories (monorepo)
taze -r

# Only check specific packages
taze -n requests,httpx

# Skip packages matching a pattern
taze -x /^pytest/

# Skip only one version range, not the whole package
taze -x requests@3

# Do not upgrade releases published in the last two weeks
taze --maturity-period 14

# Include exact == pins (they are skipped by default)
taze -l

# Sort by largest update first
taze --sort diff-desc

# Machine-readable JSON output
taze --json
```

### Options

```text
  -w, --write              Write updates back to file
  -i, --install            Install after writing (implies -w)
  -u, --update             Alias for --install
  -I, --interactive        Choose which packages to update interactively
  -r, --recursive          Scan subdirectories for pyproject.toml / requirements*.txt
      --ignore-paths <glob> Comma-separated glob paths to skip with --recursive
      --ignore-other-workspaces / --include-other-workspaces
                            Skip nested repositories and workspaces (default: skip)
  -a, --all                Show up-to-date packages too
  -n, --include <deps>     Only check these packages (comma-separated or /regex/)
  -x, --exclude <deps>     Skip these packages (comma-separated or /regex/)
  -C, --cwd <path>         Working directory
  -s, --silent             No output
  -v, --version            Show version
      --sort <type>        Sort output: name-asc | name-desc | diff-asc | diff-desc
      --fail-on-outdated   Exit with code 1 if any outdated dependencies are found
  -l, --include-locked     Include exact == version pins
      --maturity-period <n>
                            Require a release to be at least n days old
      --maturity-period-exclude <deps>
                            Bypass the maturity period for selected packages
      --config <path>      Read settings from a taze.toml file
      --concurrency <n>    Number of concurrent PyPI requests (default: 10)
      --json               Machine-readable JSON output (updates only; use -a for all)
      --force, -f          Bypass the local metadata cache
      --retry <n>          Retries after failed registry requests
      --no-retry           Disable registry retries
      --request-timeout <s>
                            Registry request timeout in seconds
      --github-actions/--no-github-actions
                            Check workflow and composite-action references
      --github-actions-style <auto|tag|sha>
                            Preserve or choose action reference style
      --github-actions-pin  Pin GitHub Actions to their commit SHA even if
                            already up to date (implies sha style)
```

---

## Output

```text
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

| File                | Section                             |
| ------------------- | ----------------------------------- |
| `pyproject.toml`    | `[project] dependencies`            |
| `pyproject.toml`    | `[project.optional-dependencies.*]` |
| `pyproject.toml`    | `[dependency-groups.*]` (PEP 735)   |
| `pyproject.toml`    | `[tool.uv.dev-dependencies]`        |
| `pyproject.toml`    | `[tool.uv.constraint-dependencies]` |
| `pyproject.toml`    | `[tool.poetry.dependencies]`        |
| `pyproject.toml`    | `[tool.poetry.group.*.dependencies]` |
| `pyproject.toml`    | `[tool.pdm.dev-dependencies.*]`     |
| `pyproject.toml`    | `[tool.hatch.envs.*.dependencies]`  |
| `requirements*.txt` | Standard pip format                 |
| `.github/workflows/*.yml` / `action.yml` | Versioned `uses:` refs |

---

## Monorepos

`taze -r` finds supported dependency files under the working directory. It skips
virtual environments, build/cache directories, and nested repositories/workspaces by
default. Use `--ignore-paths examples/**,vendor/**` for additional exclusions, or
`--include-other-workspaces` to deliberately scan nested workspaces.

Project names declared in workspace `pyproject.toml` files are recognised as local
packages and are not looked up on PyPI.

## Configuration

Place options in `taze.toml` or in the `[tool.taze]` table of `pyproject.toml`.
CLI values take precedence over project configuration.

```toml
[tool.taze]
recursive = true
ignore-paths = ["examples/**", "vendor/**"]
maturity-period = 7
maturity-period-exclude = "internal-tools"
include-locked = false

[tool.taze.package-mode]
django = "minor"
"/pytest-.*/" = "patch"
setuptools = "ignore"
```

`package-mode` accepts every mode listed above, plus `ignore`. Exact package names,
`*` globs, and slash-delimited regular expressions are supported. The cache lives
at `$XDG_CACHE_HOME/taze/pypi.json` (or `~/.cache/taze/pypi.json`) for 30 minutes;
`--force` bypasses it.

GitHub Actions use the GitHub REST API. Set `GITHUB_TOKEN` or `GH_TOKEN` to avoid
unauthenticated API limits. Branch refs, local actions, Docker actions, and
non-`v` tags are left untouched.

## Installation

`taze -i` writes updates, then chooses an installer from the project metadata:
`uv sync`, `poetry install`, `pdm install`, or `pixi install`. `-u` is an alias for
`-i`.

---

## License

MIT
