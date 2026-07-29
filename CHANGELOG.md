## 0.2.5 (2026-07-29)

### Refactor

- calculate release age once
- simplify interactive selection
- simplify dependency sorting
- parse requirements once
- simplify bump policy lookup

## 0.2.4 (2026-07-27)

### Refactor

- update version handling by moving __version__ to __init__.py and adjusting user agent string

## 0.2.3 (2026-07-27)

### Fix

- format hooks in prek.toml for improved readability
- replace date with datetime for accurate date handling
- add poethepoet

### Refactor

- simplify dependency handling by removing unused parameters and updating data structures

## 0.2.2 (2026-07-27)

### Fix

- streamline publish workflow by consolidating jobs and removing unnecessary steps
- update repository revisions and add update section in prek.toml
- add type parameter to _configured function for better type inference

## 0.2.1 (2026-07-10)

### Fix

- update type hints for better type safety in main.py and test_models.py
- move context parameter to main command

## 0.2.0 (2026-07-10)

### Feat

- skip local workspace dependencies
- scan PDM and Hatch dependency groups
- support per-package update policies
- filter updates by release maturity
- detect Python package managers for installs
- load project configuration from TOML
- add workspace-aware recursive discovery
- use rich progress bar for PyPI dependency scan

### Fix

- render JSON output and honor grouping option
- preserve PEP 508 declarations when updating
- resolve and write updates within mode policies

## 0.1.1 (2026-06-25)

### Feat

- add Python version specification
- enhance ruff configuration with isort and formatting options
- configure ruff settings in pyproject.toml
- add prek for pre-commit
- add concurrency settings for release workflow
- add cooldown configuration for dependency updates in dependabot.yml
- enhance CI workflow with concurrency and permissions
- add test job to CI workflow
- show live package count during PyPI resolution
- retry PyPI requests on transient network failures
- add --check as alias for --fail-on-outdated

### Fix

- correct formatting in ruff lint ignore list
- linting issues
- update ruff lint configuration to enhance select and ignore rules
- add missing cache_dir option in pytest configuration
- update pypa/gh-action-pypi-publish action to specific version v1.14.0
- add missing 'with' parameters for checkout and setup-uv steps in publish workflow
- replace Python 2 except syntax with Python 3 tuples
- update user agent URL in pypi.py

## 0.1.0 (2026-06-24)

### Feat

- add CI workflow for linting and formatting checks
- add core functionality for dependency management
- initialize taze CLI tool for managing Python dependencies

### Fix

- remove unused dependency 'ty' from uv.lock

### Refactor

- streamline publish workflow for PyPI
