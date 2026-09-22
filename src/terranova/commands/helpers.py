#
# Copyright 2023-2025 Elasticsearch B.V.
# Copyright 2026-present Adrien Mannocci
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import json
import os
from abc import ABC
from pathlib import Path
from typing import cast, override

import click
from click import Parameter
from click.exceptions import Exit
from click.shell_completion import CompletionItem

from terranova.binds import Git, Terraform
from terranova.engines import default_engine_manager
from terranova.exceptions import (
    EngineError,
    GitRepositoryError,
    GraphError,
    InvalidResourcesError,
    ManifestError,
)
from terranova.executor import ResourceGroupResult, ResourceGroupTask, create_executor
from terranova.graph import Wave, build_dependency_graph, compute_waves
from terranova.process import ErrorReturnCode
from terranova.resources import Resource, ResourcesFinder, ResourcesManifest, Selector
from terranova.ui import ParallelProgress
from terranova.utils import Constants, log

auto_scope_option = click.option(
    "--auto-scope",
    "-A",
    help="Scope to resource groups affected by the current git diff (working "
    + "tree and staged changes vs HEAD, plus untracked files) instead of an "
    + "explicit `path`.",
    is_flag=True,
)
"""Shared `--auto-scope`/`-A` option for `plan`, `apply`, `destroy` and `docs`."""

flat_strategy_option = click.option(
    "--strategy",
    help="Execution strategy across resource groups: `sequential` (default) runs "
    + "one after another; `parallel` runs them concurrently.",
    type=click.Choice(["sequential", "parallel"], case_sensitive=False),
    default="sequential",
)
"""Shared `--strategy` option for commands with no cross-project dependency
ordering concern (`fmt`, `validate` - see `terranova.commands.execution.flat_wave`).
`plan`/`apply` define their own, worded around dependency order."""

flat_group_concurrency_option = click.option(
    "--group-concurrency",
    help="With `--strategy parallel`, the maximum number of resource groups run "
    + "concurrently. Defaults to a sane pool size if unset.",
    type=int,
    default=None,
)
"""Shared `--group-concurrency` option, paired with `flat_strategy_option`."""


class SelectorType(click.ParamType[Selector]):
    """Selector param typing for click."""

    name: str = "selector"

    @override
    def convert(
        self, value: object, param: Parameter | None, ctx: click.Context | None
    ) -> Selector:
        if not isinstance(value, str):
            self.fail(f"{value!r} isn't a valid selector", param, ctx)

        data = value.split("=", maxsplit=1)
        return Selector(name=data[0], value=None if len(data) == 1 else data[1])


def _completion_resources_dir(ctx: click.Context) -> Path:
    """Resolve the resources dir from the root `--conf-dir` during shell completion."""
    conf_dir = cast("Path", ctx.find_root().params["conf_dir"])
    return conf_dir / "resources"


def complete_resource_path(
    ctx: click.Context, param: Parameter, incomplete: str
) -> list[CompletionItem]:
    """Shell completion of resource group paths, relative to the resources dir."""
    _ = param
    try:
        resources_dir = _completion_resources_dir(ctx)
        rel_paths = sorted(rel for _, rel in find_all_resource_dirs(resources_dir))
    except OSError:
        return []
    return [CompletionItem(rel) for rel in rel_paths if rel.startswith(incomplete)]


def complete_runbook_name(
    ctx: click.Context, param: Parameter, incomplete: str
) -> list[CompletionItem]:
    """Shell completion of runbook names of the resource group given as `path`."""
    _ = param
    path = cast("str | None", ctx.params.get("path"))
    if not path:
        return []
    manifest_path = _completion_resources_dir(ctx) / path / Constants.MANIFEST_FILE_NAME
    try:
        manifest = ResourcesManifest.from_file(manifest_path)
    except (ManifestError, OSError):
        return []
    return [
        CompletionItem(runbook.name)
        for runbook in manifest.runbooks or []
        if runbook.name.startswith(incomplete)
    ]


def read_manifest(path: Path) -> "ResourcesManifest":
    """
    Read the resources manifest if possible.
    This function handle errors by logging and exiting.

    Args:
        path: path to manifest directory.

    Returns:
        the manifest.
    """
    try:
        return ResourcesManifest.from_file(path / Constants.MANIFEST_FILE_NAME)
    except ManifestError as err:
        log.fatal("read manifest", err)


def parse_execution_plan(text: str) -> dict[str, str]:
    """
    Parse a saved `.tnplan` file's content into `rel_path -> base64 plan bytes`.

    The file path comes straight from the CLI argument, so its content is
    untrusted input (hand-edited, corrupted, or from an unrelated JSON file) -
    validate its shape instead of casting `json.loads()`'s `Any` result
    straight to `dict[str, str]`, which would just push a confusing failure
    (e.g. `b64decode()` choking on a non-string value) further downstream.

    Raises:
        ValueError: if `text` isn't valid JSON.
        TypeError: if `text` is valid JSON but not a flat object of string values.
    """
    raw = cast("object", json.loads(text))
    if not isinstance(raw, dict):
        raise TypeError("Not a valid .tnplan file: expected a JSON object.")
    execution_plan: dict[str, str] = {}
    for key, value in cast("dict[object, object]", raw).items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError(
                "Not a valid .tnplan file: expected string keys and values."
            )
        execution_plan[key] = value
    return execution_plan


def write_execution_plan(out: Path, execution_plan: dict[str, str]) -> None:
    """Write an execution plan to a `.tnplan` file, the counterpart to `parse_execution_plan`."""
    out.write_text(json.dumps(execution_plan))


def discover_resources(
    path: Path, selectors: list[Selector] | None = None
) -> list[Resource]:
    """
    Discover resources in every terraform configuration files.
    This function handle errors by logging and exiting.

    Args:
        path: path to resources directory.
        selectors: list of selectors.

    Returns:
        list of resources.
    """
    try:
        return ResourcesFinder.find_in_dir(path, selectors)
    except InvalidResourcesError as err:
        log.fatal(
            f"discover resources at `{path.as_posix()}`",
            err,
        )


def find_all_resource_dirs(
    resources_dir: Path, search_dir: Path | None = None
) -> list[tuple[Path, str]]:
    """
    Find all path where there is a resource manifest.

    Args:
        resources_dir: the resources root, relative paths are computed from it.
        search_dir: restrict the search to this directory (defaults to
            `resources_dir`).

    Returns:
        list of all path.
    """
    paths: list[tuple[Path, str]] = []
    resources_dir_path = resources_dir.as_posix()
    resources_dir_prefix_len = len(resources_dir_path) + 1
    for path, _, files in os.walk(search_dir or resources_dir):
        for file in files:
            if os.path.basename(file) == Constants.MANIFEST_FILE_NAME:
                paths.append((Path(path), path[resources_dir_prefix_len:]))
    return paths


def resource_dirs(resources_dir: Path, path: str | None) -> list[tuple[Path, str]]:
    """
    List of all resource dirs to interact with.

    Args:
        resources_dir: the resources root.
        path: use a specific path.

    Returns:
        list of all resource dirs.
    """
    search_dir = resources_dir.joinpath(path) if path else resources_dir
    return find_all_resource_dirs(resources_dir, search_dir)


def _match_resource_dirs(
    resources_dir: Path,
    all_dirs: list[tuple[Path, str]],
    changed_files: list[Path],
) -> list[tuple[Path, str]]:
    """Map each changed file to its nearest ancestor resource-group dir, deduped."""
    resources_root = resources_dir.resolve()
    by_full_path = {
        full_path.resolve(): (full_path, rel_path) for full_path, rel_path in all_dirs
    }

    matched: dict[Path, tuple[Path, str]] = {}
    for changed_file in changed_files:
        current = changed_file.resolve().parent
        while True:
            if current in by_full_path:
                matched[current] = by_full_path[current]
                break
            if current == resources_root or resources_root not in current.parents:
                break
            current = current.parent

    return sorted(matched.values(), key=lambda entry: entry[1])


def auto_scope_resource_dirs(
    conf_dir: Path, resources_dir: Path
) -> list[tuple[Path, str]]:
    """
    Scope resource dirs to those affected by the current git diff (working
    tree and staged changes vs HEAD, including untracked files).
    This function handle errors by logging and exiting.
    """
    # `conf_dir` is guaranteed to exist (`--conf-dir` requires it), unlike
    # `resources_dir` (e.g. before a first `terranova init`) - using it as
    # `cwd` avoids a spurious `CommandNotFound` from `Popen` failing to chdir.
    git = Git(conf_dir)
    try:
        root = Path(git.repo_root())
    except ErrorReturnCode:
        log.fatal(
            f"resolve the git repository at `{conf_dir.as_posix()}`",
            GitRepositoryError(conf_dir),
        )
    git.cwd(root)
    changed_files = [root / rel for rel in git.changed_files()]

    all_dirs = find_all_resource_dirs(resources_dir)
    return _match_resource_dirs(resources_dir, all_dirs, changed_files)


def resolve_resource_dirs(
    conf_dir: Path, resources_dir: Path, path: str | None, auto_scope: bool
) -> list[tuple[Path, str]]:
    """Shared `path`/`--auto-scope` resolution for `plan`, `destroy` and `docs`."""
    if auto_scope and path:
        raise click.UsageError(
            "`--auto-scope`/`-A` can't be combined with an explicit `path`."
        )
    if auto_scope:
        return auto_scope_resource_dirs(conf_dir, resources_dir)
    return resource_dirs(resources_dir, path)


def mount_context(
    full_path: Path,
    resources_dir: Path,
    plugin_cache_dir: Path,
    verbose: bool = False,
    manifest: ResourcesManifest | None = None,
    import_vars: bool = False,
) -> Terraform:
    """Mount the terraform context by importing variables if needed."""
    # Ensure manifest exists and can be read
    if not manifest:
        manifest = read_manifest(full_path)

    # Import variables
    variables = None
    if import_vars:
        variables = extract_import_vars(
            manifest, resources_dir, plugin_cache_dir, verbose
        )
    # Cache hit when `EngineManager.prepare` already ran (plan/apply), so no download here
    try:
        binary = default_engine_manager().resolve(manifest.engine)
    except EngineError as err:
        log.fatal("resolve terraform engine", err)
    return Terraform(full_path, plugin_cache_dir, variables, verbose, binary)


class TerraformTask(ResourceGroupTask, ABC):
    """A `ResourceGroupTask` that runs terraform against its resource group."""

    def __init__(
        self,
        full_path: Path,
        rel_path: str,
        resources_dir: Path,
        plugin_cache_dir: Path,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        """Init terraform task."""
        super().__init__(full_path, rel_path, quiet=quiet)
        self._resources_dir: Path = resources_dir
        self._plugin_cache_dir: Path = plugin_cache_dir
        self._verbose: bool = verbose

    def mount(
        self,
        manifest: ResourcesManifest | None = None,
        import_vars: bool = False,
    ) -> Terraform:
        """Mount the terraform context of this task's resource group."""
        return mount_context(
            self.full_path,
            self._resources_dir,
            self._plugin_cache_dir,
            self._verbose,
            manifest=manifest,
            import_vars=import_vars,
        )


def extract_import_vars(
    manifest: ResourcesManifest,
    resources_dir: Path,
    plugin_cache_dir: Path,
    verbose: bool = False,
) -> dict[str, str]:
    """Extract import variables from manifest."""
    variables: dict[str, str] = {}
    if manifest.imports:
        for importer in manifest.imports:
            target = importer.target if importer.target else importer.resource
            variables[target] = extract_output_var(
                importer.source,
                importer.resource,
                resources_dir,
                plugin_cache_dir,
                verbose,
            )
    return variables


def extract_output_var(
    path: str,
    name: str,
    resources_dir: Path,
    plugin_cache_dir: Path,
    verbose: bool = False,
) -> str:
    """Show output values from your root module."""
    # Construct resources path
    full_path = resources_dir.joinpath(path)

    # Mount terraform context
    terraform = mount_context(full_path, resources_dir, plugin_cache_dir, verbose)

    # Execute output command
    try:
        return terraform.output(name)
    except ErrorReturnCode as err:
        raise Exit(code=err.exit_code) from err


def execute_tasks(
    strategy: str,
    tasks: list[ResourceGroupTask],
    fail_at_end: bool,
    waves: list[Wave],
    group_concurrency: int | None,
) -> list[ResourceGroupResult]:
    """
    Run `tasks` with the selected strategy.

    Owns the live status display's lifecycle for the parallel executor - the
    executor itself never renders anything, see `terranova.ui.ParallelProgress`
    and `terranova.executor.ExecutorObserver`.
    """
    if strategy == "parallel":
        ui = ParallelProgress(total=len(tasks))
        executor = create_executor(
            "parallel", max_workers=group_concurrency, observer=ui
        )
        with ui:
            return executor.run(tasks, fail_at_end, waves=waves)
    return create_executor("sequential").run(tasks, fail_at_end, waves=waves)


def flat_wave(paths: list[tuple[Path, str]]) -> list[Wave]:
    """
    A single wave containing every discovered rel_path.

    For commands with no cross-project dependency ordering concern - `fmt` and
    `validate` never resolve manifest `imports` (they don't call `mount_context`
    with `import_vars=True`), so there's nothing to build a dependency graph
    from and every project is safe to run concurrently with every other.
    """
    return [[rel_path for _, rel_path in paths]]


def read_manifests_and_waves(
    paths: list[tuple[Path, str]],
) -> tuple[dict[str, ResourcesManifest], list[Wave]]:
    """
    Read every manifest once and compute dependency-ordered waves from it.

    Used by `plan`/`apply` - the only commands that resolve manifest `imports`
    (via `mount_context(..., import_vars=True)`) and therefore need dependency
    ordering; see `flat_wave` for commands that don't.
    """
    manifests = {rel_path: read_manifest(full_path) for full_path, rel_path in paths}

    # Download pinned engines before the executor phase, so tasks never race on it
    try:
        default_engine_manager().prepare(manifests.values())
    except EngineError as err:
        log.fatal("prepare terraform engines", err)
    graph = build_dependency_graph(paths, manifests)
    try:
        waves = compute_waves(graph)
    except GraphError as err:
        log.fatal("compute execution waves", err)
    return manifests, waves
