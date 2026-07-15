# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Terranova** is a thin wrapper around Terraform that provides tools and logic to handle Terraform configurations at scale. It allows managing infrastructure as code without using Terraform modules, enabling arbitrary resource layouts and auto-generated documentation.

This is a fork of [elastic/terranova](https://github.com/elastic/terranova) under active stewardship for community improvements.

## Architecture Overview

### Core Design Pattern

Terranova uses a **Command → Bind → Process** architecture:

1. **Commands** (`src/terranova/commands/binds.py`): CLI entry points that parse user input and orchestrate operations
2. **Binds** (`src/terranova/binds.py`): Abstraction layer that wraps external tools (mainly Terraform)
   - `Terraform` class: Inherits from `Bind`, wraps terraform CLI
   - `Bind` class: Base abstraction for command execution
3. **Process** (`src/terranova/process.py`): Low-level process execution and environment management
   - `Command`: Represents a single command execution
   - `Bind`: Base class for tool bindings

### Key Components

- **CLI Layer** (`src/terranova/cli.py`): Click-based CLI that loads commands and sets up shared context
- **Resources** (`src/terranova/resources.py`): Manifest parsing and validation using dataclasses and `@serde`
  - Defines YAML manifest structure: metadata, dependencies, runbooks, imports
  - Uses jsonschema for validation (candidate for marshmallow replacement)
  - Handles symlink management for shared dependencies
- **Exceptions** (`src/terranova/exceptions.py`): Custom exception hierarchy for error handling
- **Utils** (`src/terranova/utils.py`): Shared utilities including logging and context management
  - `SharedContext`: Thread-safe global state (conf dir, terraform cache, etc.)
  - `Log`: Colored output wrapper around rich

### Directory Structure

```
src/terranova/
├── cli.py              # Main CLI entry point (Click group)
├── binds.py           # Terraform binding/wrapper
├── resources.py       # Manifest models and validation
├── process.py         # Process execution abstraction
├── exceptions.py      # Custom exceptions
├── utils.py           # Logging, context, serde decorator
├── io.py              # I/O utilities
├── commands/          # CLI command implementations
│   ├── binds.py      # Command handlers (init, apply, plan, etc.)
│   └── helpers.py    # Shared command utilities (discovery, mounting)
├── schemas/          # JSON schema definitions for manifest validation
└── templates/        # Jinja2 templates for documentation generation
```

## Development Commands

All commands use the `uv` build system and `poethepoet` task runner (via `poe`).

### Setup & Environment

```bash
# Install dependencies and setup environment
uv sync
uv run poe env:configure

# Upgrade all dependencies
uv run poe project:upgrade

# Add Apache license headers to new files
uv run poe project:license
```

### Code Quality

```bash
# Lint with pylint
uv run poe lint

# Format code (import sorting + black-style formatting)
uv run poe fmt

# Type check with basedpyright
uv run basedpyright
```

### Testing

```bash
# Run all tests (parallel with xdist, 15-min timeout per test)
uv run poe test

# Run e2e tests only
uv run poe test:e2e

# Run single test file
pytest tests/it/test_process.py

# Run single test
pytest tests/it/test_process.py::TestCommand::test_empty

# Run tests matching a pattern
pytest -k "test_load" -v

# Run with verbose output and show print statements
pytest -vv -s tests/it/test_resources.py::test_function_name
```

### Building

```bash
# Generate PyInstaller configuration
uv run poe generate

# Build standalone binary (depends on env:wipe)
uv run poe build

# Clean build artifacts
uv run poe env:wipe
```

### Release

```bash
# Create PR with changes for release
uv run poe release:pre

# Create a new release
uv run poe release

# Prepare next iteration (bump version, etc.)
uv run poe release:post
```

## Key Dependencies

- **click**: CLI framework (defines commands and options)
- **pyserde**: Serialization/deserialization with `@serde` decorator on dataclasses
- **jsonschema**: Manifest validation (planned migration to marshmallow)
- **jinja2**: Template rendering for documentation
- **envyaml**: YAML parsing with environment variable interpolation
- **rich**: Terminal formatting and logging
- **mdformat**: Markdown formatting for docs

## Important Patterns & Conventions

### Manifest Files

Terranova uses YAML manifest files (`manifest.yml`) to define resources:

```yaml
version: "1.2"
metadata:
  name: Resource Group Name
  description: Description
  url: https://...
  contact: mailto:...

dependencies:
  - source: providers/github.tf
    target: 00-github-provider.tf

runbooks:
  - name: runbook_name
    entrypoint: tool
    workdir: subdir
    args: [--flag]
    env:
      - name: VAR_NAME
        value: value

imports:
  - from: ../other_group
    import: output_var_name
    as: input_var_name
```

Schemas are stored in `src/terranova/schemas/` and loaded via `pkgutil` to validate manifest YAML against jsonschema.

### Dataclass Serialization

All data models use `@serde` decorator from pyserde:

```python
@serde
@dataclass(frozen=True)
class MyModel:
    field: str
    optional_field: str | None = None
```

This enables automatic YAML/dict conversion without manual serialization code.

### Logging

Use the `Log` class from utils:

```python
from terranova.utils import Log

Log.action("message")          # Yellow action marker
Log.success("message")         # Green success ("Succeeded to {message}")
Log.failure("message", err)    # Red failure, prints ExplainedError cause/resolution if present
Log.fatal("message", err)      # Red failure + raises Exit(1)
```

### Error Handling

Custom exceptions in `src/terranova/exceptions.py`:

- `ExplainedError`: Base for all errors that carry a `cause` and optional `resolution` shown to the user
- `ManifestError` / `InvalidManifestError`: Manifest validation failed
- `InvalidResourcesError`: Resource configuration invalid
- `MissingManifestError`: Manifest file not found
- `UnreadableManifestError`: Cannot read manifest
- `VersionManifestError`: Unsupported manifest version
- `RunbookError` / `AmbiguousRunbookError`: Multiple runbooks match a name
- `MissingRunbookError`: Named runbook not found
- `MissingRunbookEnvError`: Required env var for runbook missing

Wrap jsonschema `ValidationError` in `InvalidManifestError` when catching.

### Symlink Management

Terranova automatically creates symlinks for dependencies during `init`:

- Parent directories are created if missing
- Symlinks updated each time `init` runs
- Handles both file and directory sources

## Testing Structure

Tests organized in `tests/`:

- `tests/it/`: Integration tests using fixtures and test resources
- `tests/e2e/`: End-to-end tests with actual Terraform execution

Use pytest fixtures from `conftest.py` for common test setup (temp directories, resources, etc.).

## Code Style

- **Formatting**: `ruff format` (black-compatible)
- **Imports**: Sorted with `ruff check --select I`
- **Type Hints**: Full type hints required (checked with basedpyright)
- **Line Length**: Implicit (ruff default 88)
- **Docstrings**: Present on public methods and classes
- **Comments**: Only where logic isn't self-evident

## Debugging

Set `--debug` flag at CLI level to enable debug mode:

```bash
terranova --debug --conf-dir ./conf init resource_group
```

This sets `SharedContext.debug = True` which can be checked in code.

## Common Issues & Fixes

### Symlink Creation Fails
- Check parent directories exist
- Verify file permissions on shared resources
- See recent commits on symlink parent directory handling

### Manifest Validation Fails
- Validate YAML syntax in manifest.yml
- Check version field matches manifest version in schemas
- Verify all required fields present in metadata

### Tests Fail Due to Missing State
- Run `uv sync` to ensure dev dependencies installed
- Check terraform binary is in PATH
- For e2e tests, ensure test resources in proper layout

## Notes for Contributors

- This is a fork actively maintained for community improvements
- Apache-2.0 licensed
- Original source: [elastic/terranova](https://github.com/elastic/terranova)
- Keep copyright notices for both Elasticsearch and current maintainer
- Follow existing commit message format: `type: description` (e.g., `fix:`, `refactor:`, `chore:`, `docs:`)

## GitHub PR/Issue Creation

Before running `gh pr create`, use the `pr-template-validation` skill to check the PR body against `.github/PULL_REQUEST_TEMPLATE.md`.

Before running `gh issue create`, use the `issue-template-validation` skill to check the issue body against the matching `.github/ISSUE_TEMPLATE/*` template.

Both skills reject placeholder text (TBD/TODO/FIXME/WIP), empty sections, and duplicated content across sections — do not bypass them by creating the PR/issue directly without invoking the skill first.