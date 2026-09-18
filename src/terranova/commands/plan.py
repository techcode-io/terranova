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
from base64 import b64encode
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import override

import click
from click.exceptions import Exit

from terranova.binds import TerraformChangeError
from terranova.commands.helpers import (
    TerraformTask,
    auto_scope_option,
    execute_tasks,
    read_manifests_and_waves,
    resolve_resource_dirs,
    write_execution_plan,
)
from terranova.executor import ResourceGroupResult, ResourceGroupTask
from terranova.resources import ResourcesManifest
from terranova.utils import AppContext, Constants, log


class _PlanTask(TerraformTask):
    """Generates one project's plan."""

    def __init__(
        self,
        full_path: Path,
        rel_path: str,
        resources_dir: Path,
        plugin_cache_dir: Path,
        verbose: bool,
        manifest: ResourcesManifest | None,
        *,
        input: bool,
        no_color: bool,
        parallelism: int,
        detailed_exitcode: bool,
        out: Path | None,
        execution_plan: dict[str, str],
        quiet: bool = False,
    ) -> None:
        """Init plan task."""
        super().__init__(
            full_path, rel_path, resources_dir, plugin_cache_dir, verbose, quiet=quiet
        )
        self._manifest: ResourcesManifest | None = manifest
        self._input: bool = input
        self._no_color: bool = no_color
        self._parallelism: int = parallelism
        self._detailed_exitcode: bool = detailed_exitcode
        self._out: Path | None = out
        self._execution_plan: dict[str, str] = execution_plan

    @override
    def run(self) -> None:
        if not self.quiet:
            log.action(f"Generating plan: {self.rel_path}")

        # Mount terraform context
        terraform = self.mount(manifest=self._manifest, import_vars=True)

        if self._out:
            with NamedTemporaryFile(prefix="terranova-") as file_descriptor:
                resolved_path = Path(file_descriptor.name)
                try:
                    terraform.plan(
                        input=self._input,
                        no_color=self._no_color,
                        parallelism=self._parallelism,
                        detailed_exitcode=self._detailed_exitcode,
                        out=resolved_path,
                        rel_path=self.rel_path,
                        quiet=self.quiet,
                    )
                    self._execution_plan[self.rel_path] = b64encode(
                        resolved_path.read_bytes()
                    ).decode(Constants.ENCODING_UTF_8)
                except TerraformChangeError as plan_err:
                    if plan_err.exit_code == 2:
                        self._execution_plan[self.rel_path] = b64encode(
                            resolved_path.read_bytes()
                        ).decode(Constants.ENCODING_UTF_8)
                    raise
        else:
            terraform.plan(
                input=self._input,
                no_color=self._no_color,
                parallelism=self._parallelism,
                detailed_exitcode=self._detailed_exitcode,
                rel_path=self.rel_path,
                quiet=self.quiet,
            )


@click.command("plan")
@click.argument("path", type=str, required=False)
@auto_scope_option
@click.option(
    "--input/--no-input",
    help="Ask for input for variables if not directly set.",
    default=True,
)
@click.option(
    "--no-color", help="If specified, output won't contain any color.", is_flag=True
)
@click.option(
    "--parallelism",
    help="Limit the number of parallel resource operations.",
    type=int,
    default=10,
)
@click.option(
    "--fail-at-end",
    help="If specified, only fail afterwards; allow all non-impacted projects to continue.",
    default=False,
    is_flag=True,
)
@click.option(
    "--detailed-exitcode",
    help="""
    \b
    Return detailed exit codes when the command exits.
    This will change the meaning of exit codes to:
    0 - Succeeded, diff is empty (no changes)
    1 - Errored
    2 - Succeeded, there is a diff
    """,
    is_flag=True,
)
@click.option(
    "--out",
    help="Write a plan file to the given path",
    type=click.Path(path_type=Path, dir_okay=False, writable=True),
    required=False,
)
@click.option(
    "--strategy",
    help="Execution strategy across resource groups: `sequential` (default) runs "
    + "one after another; `parallel` runs independent groups concurrently. Both "
    + "respect dependency order derived from `imports`.",
    type=click.Choice(["sequential", "parallel"], case_sensitive=False),
    default="sequential",
)
@click.option(
    "--group-concurrency",
    help="With `--strategy parallel`, the maximum number of resource groups run "
    + "concurrently within a wave. Distinct from `--parallelism`, which limits "
    + "resource-level concurrency inside a single terraform invocation. "
    + "Defaults to a sane pool size if unset.",
    type=int,
    default=None,
)
@click.pass_obj
def plan(
    ctx: AppContext,
    path: str | None,
    auto_scope: bool,
    input: bool,
    no_color: bool,
    parallelism: int,
    fail_at_end: bool,
    detailed_exitcode: bool,
    out: Path | None,
    strategy: str,
    group_concurrency: int | None,
) -> None:
    """Show changes required by the current configuration."""
    # Find all resources manifests
    paths = resolve_resource_dirs(ctx.conf_dir, ctx.resources_dir, path, auto_scope)

    # Execution plan
    execution_plan: dict[str, str] = {}

    # Read every manifest once, reused for mounting and for the dependency graph
    manifests, waves = read_manifests_and_waves(paths)
    quiet = strategy == "parallel"
    tasks: list[ResourceGroupTask] = [
        _PlanTask(
            full_path,
            rel_path,
            ctx.resources_dir,
            ctx.terraform_shared_plugin_cache_dir,
            ctx.verbose,
            manifests[rel_path],
            input=input,
            no_color=no_color,
            parallelism=parallelism,
            detailed_exitcode=detailed_exitcode,
            out=out,
            execution_plan=execution_plan,
            quiet=quiet,
        )
        for full_path, rel_path in paths
    ]

    results = execute_tasks(strategy, tasks, fail_at_end, waves, group_concurrency)

    def save_plan_to_file():
        if not out:
            return
        write_execution_plan(out, execution_plan)
        log.action(
            f"Saved terranova plan to: {out}\n\n"
            + "To perform exactly these actions with terranova, run the following command to apply:\n"
            + f'    terranova apply "{out}"'
        )

    failures: list[ResourceGroupResult] = [r for r in results if r.status == "failed"]
    if failures:
        error_exit_codes = [r.exit_code for r in failures if r.exit_code is not None]
        # The error_exit_codes list contains the numbers 1, 2, or both if detailed-exitcode is enabled.
        # See https://developer.hashicorp.com/terraform/cli/commands/plan#detailed-exitcode fur further details.
        # If 1 is present, the plan failed for at least one path, hence we should return 1.
        # If all exit codes are 2, the plan succeeded for all paths, but there are changes, hence we should return 2.
        exit_code = 1 if 1 in error_exit_codes else 2
        # Exit code 2 is a success, but there are changes. Hence, we should write the plan to the given path.
        if exit_code == 2:
            save_plan_to_file()
        raise Exit(code=exit_code)

    # Generate terranova plan
    save_plan_to_file()
