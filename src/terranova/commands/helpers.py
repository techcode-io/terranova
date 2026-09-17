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
from pathlib import Path
from typing import cast, override

import click
from click import Parameter
from click.exceptions import Exit

from terranova.binds import Terraform
from terranova.exceptions import InvalidResourcesError, ManifestError
from terranova.executor import ResourceGroupResult, ResourceGroupTask, create_executor
from terranova.graph import Wave, build_dependency_graph, compute_waves
from terranova.process import ErrorReturnCode
from terranova.resources import Resource, ResourcesFinder, ResourcesManifest, Selector
from terranova.ui import ParallelProgress
from terranova.utils import AppContext, Constants

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


def read_manifest(ctx: AppContext, path: Path) -> "ResourcesManifest":
    """
    Read the resources manifest if possible.
    This function handle errors by logging and exiting.

    Args:
        ctx: the application context.
        path: path to manifest directory.

    Returns:
        the manifest.
    """
    try:
        return ResourcesManifest.from_file(path / Constants.MANIFEST_FILE_NAME)
    except ManifestError as err:
        ctx.log.fatal("read manifest", err)


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
    ctx: AppContext, path: Path, selectors: list[Selector] | None = None
) -> list[Resource]:
    """
    Discover resources in every terraform configuration files.
    This function handle errors by logging and exiting.

    Args:
        ctx: the application context.
        path: path to resources directory.
        selectors: list of selectors.

    Returns:
        list of resources.
    """
    try:
        return ResourcesFinder.find_in_dir(path, selectors)
    except InvalidResourcesError as err:
        ctx.log.fatal(
            f"discover resources at `{path.as_posix()}`",
            err,
        )


def find_all_resource_dirs(
    ctx: AppContext, resources_dir: Path
) -> list[tuple[Path, str]]:
    """
    Find all path where there is a resource manifest.

    Returns:
        list of all path.
    """
    paths: list[tuple[Path, str]] = []
    resources_dir_path = ctx.resources_dir.as_posix()
    resources_dir_prefix_len = len(resources_dir_path) + 1
    for path, _, files in os.walk(resources_dir):
        for file in files:
            if os.path.basename(file) == Constants.MANIFEST_FILE_NAME:
                paths.append((Path(path), path[resources_dir_prefix_len:]))
    return paths


def resource_dirs(ctx: AppContext, path: str | None) -> list[tuple[Path, str]]:
    """
    List of all resource dirs to interact with.

    Args:
        ctx: the application context.
        path: use a specific path.

    Returns:
        list of all resource dirs.
    """
    resources_dir = ctx.resources_dir
    if path:
        resources_dir = resources_dir.joinpath(path)
    return find_all_resource_dirs(ctx, resources_dir)


def mount_context(
    ctx: AppContext,
    full_path: Path,
    manifest: ResourcesManifest | None = None,
    import_vars: bool = False,
) -> Terraform:
    """Mount the terraform context by importing variables if needed."""
    # Ensure manifest exists and can be read
    if not manifest:
        manifest = read_manifest(ctx, full_path)

    # Import variables
    variables = extract_import_vars(ctx, manifest) if import_vars else None
    return Terraform(ctx, full_path, variables)


def extract_import_vars(ctx: AppContext, manifest: ResourcesManifest) -> dict[str, str]:
    """Extract import variables from manifest."""
    variables: dict[str, str] = {}
    if manifest.imports:
        for importer in manifest.imports:
            target = importer.target if importer.target else importer.resource
            variables[target] = extract_output_var(
                ctx, importer.source, importer.resource
            )
    return variables


def extract_output_var(ctx: AppContext, path: str, name: str) -> str:
    """Show output values from your root module."""
    # Construct resources path
    full_path = ctx.resources_dir.joinpath(path)

    # Mount terraform context
    terraform = mount_context(ctx, full_path)

    # Execute output command
    try:
        return terraform.output(name)
    except ErrorReturnCode as err:
        raise Exit(code=err.exit_code) from err


def execute_tasks(
    ctx: AppContext,
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
        ui = ParallelProgress(ctx, total=len(tasks))
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
    ctx: AppContext,
    paths: list[tuple[Path, str]],
) -> tuple[dict[str, ResourcesManifest], list[Wave]]:
    """
    Read every manifest once and compute dependency-ordered waves from it.

    Used by `plan`/`apply` - the only commands that resolve manifest `imports`
    (via `mount_context(..., import_vars=True)`) and therefore need dependency
    ordering; see `flat_wave` for commands that don't.
    """
    manifests = {
        rel_path: read_manifest(ctx, full_path) for full_path, rel_path in paths
    }
    graph = build_dependency_graph(paths, manifests)
    waves = compute_waves(graph)
    return manifests, waves
