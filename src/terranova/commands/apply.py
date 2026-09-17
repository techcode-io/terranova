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
from base64 import b64decode
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import override

import click
from click.exceptions import Exit

from terranova.commands.helpers import (
    execute_tasks,
    mount_context,
    parse_execution_plan,
    read_manifests_and_waves,
    resource_dirs,
)
from terranova.exceptions import InteractiveApprovalError
from terranova.executor import ResourceGroupTask
from terranova.resources import ResourcesManifest
from terranova.utils import Constants, Log, SharedContext


class _ApplyTask(ResourceGroupTask):
    """Applies one project's plan."""

    def __init__(
        self,
        full_path: Path,
        rel_path: str,
        manifest: ResourcesManifest | None,
        *,
        auto_approve: bool,
        target: str,
        execution_plan: dict[str, str] | None,
        quiet: bool = False,
    ) -> None:
        """Init apply task."""
        super().__init__(full_path, rel_path, quiet=quiet)
        self._manifest: ResourcesManifest | None = manifest
        self._auto_approve: bool = auto_approve
        self._target: str = target
        self._execution_plan: dict[str, str] | None = execution_plan

    @override
    def run(self) -> None:
        if not self.quiet:
            Log.action(f"Applying plan: {self.rel_path}")

        # Mount terraform context
        terraform = mount_context(
            self.full_path, manifest=self._manifest, import_vars=True
        )

        if self._execution_plan:
            with NamedTemporaryFile(prefix="terranova-") as file_descriptor:
                path = Path(file_descriptor.name)
                path.write_bytes(b64decode(self._execution_plan[self.rel_path]))
                terraform.apply(
                    plan=file_descriptor.name,
                    auto_approve=self._auto_approve,
                    target=self._target,
                    rel_path=self.rel_path,
                    quiet=self.quiet,
                )
        else:
            terraform.apply(
                auto_approve=self._auto_approve,
                target=self._target,
                rel_path=self.rel_path,
                quiet=self.quiet,
            )


@click.command("apply")
@click.argument("path_or_plan", type=str, required=False)
@click.option(
    "--auto-approve",
    help="Skip interactive approval of plan before applying.",
    is_flag=True,
)
@click.option("--target", help="Apply changes for specific target.", type=str)
@click.option(
    "--fail-at-end",
    help="If specified, only fail afterwards; allow all non-impacted projects to continue.",
    default=False,
    is_flag=True,
)
@click.option(
    "--strategy",
    help="Execution strategy across resource groups: `sequential` (default) runs "
    + "one after another; `parallel` runs independent groups concurrently. Both "
    + "respect dependency order derived from `imports`, including when applying "
    + "a saved `.tnplan` file.",
    type=click.Choice(["sequential", "parallel"], case_sensitive=False),
    default="sequential",
)
@click.option(
    "--group-concurrency",
    help="With `--strategy parallel`, the maximum number of resource groups run "
    + "concurrently within a wave. Distinct from terraform's own per-process "
    + "resource concurrency. Defaults to a sane pool size if unset.",
    type=int,
    default=None,
)
def apply(
    path_or_plan: str | None,
    auto_approve: bool,
    target: str,
    fail_at_end: bool,
    strategy: str,
    group_concurrency: int | None,
) -> None:
    """Create or update resources."""
    # Check if there is a plan to apply
    execution_plan: dict[str, str] | None
    paths: list[tuple[Path, str]]
    if path_or_plan and path_or_plan.endswith("tnplan"):
        execution_plan = parse_execution_plan(
            Path(path_or_plan).read_text(Constants.ENCODING_UTF_8)
        )
        paths = [
            (SharedContext.resources_dir().joinpath(rel_path), rel_path)
            for rel_path in execution_plan
        ]
    else:
        execution_plan = None

        # Find all resources manifests
        paths = resource_dirs(path_or_plan)

    # Running several `terraform apply` processes at once means none of them
    # can fall back to an interactive approval prompt - fail fast instead of
    # letting every task hit terraform's own cryptic error.
    if strategy == "parallel" and not auto_approve and execution_plan is None:
        Log.fatal("apply resources in parallel", InteractiveApprovalError())

    # Read every manifest once, reused for mounting and for the dependency graph.
    # Manifests are read from disk by rel_path regardless of whether `paths` came
    # from resource_dirs() or a saved .tnplan file's own keys.
    manifests, waves = read_manifests_and_waves(paths)
    quiet = strategy == "parallel"
    tasks: list[ResourceGroupTask] = [
        _ApplyTask(
            full_path,
            rel_path,
            manifests[rel_path],
            auto_approve=auto_approve,
            target=target,
            execution_plan=execution_plan,
            quiet=quiet,
        )
        for full_path, rel_path in paths
    ]

    results = execute_tasks(strategy, tasks, fail_at_end, waves, group_concurrency)

    # Report any errors if fail_at_end has been enabled
    if any(r.status == "failed" for r in results):
        raise Exit(code=1)
