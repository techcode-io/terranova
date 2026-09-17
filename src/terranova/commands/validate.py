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
from typing import override

import click

from terranova.commands.helpers import (
    discover_resources,
    execute_tasks,
    flat_group_concurrency_option,
    flat_strategy_option,
    flat_wave,
    mount_context,
    resource_dirs,
)
from terranova.exceptions import InvalidResourcesError
from terranova.executor import ResourceGroupTask
from terranova.process import ErrorReturnCode
from terranova.utils import Log


class _ValidateTask(ResourceGroupTask):
    """Validates one project's configuration."""

    @override
    def run(self) -> None:
        if not self.quiet:
            Log.action(f"Validating: {self.rel_path}")

        # Mount terraform context
        terraform = mount_context(self.full_path)
        discover_resources(self.full_path)

        message = f"validate resources at `{self.full_path.as_posix()}`."

        try:
            result = terraform.validate()
        except InvalidResourcesError as err:
            Log.failure(message, err)
            raise ErrorReturnCode(cmd=f"validate {self.rel_path}", exit_code=1) from err

        if result.valid:
            if not self.quiet:
                Log.success(message)
            return

        Log.failure(
            [message, *(f"{d.severity}: {d.summary}" for d in result.diagnostics)]
        )
        raise ErrorReturnCode(cmd=f"validate {self.rel_path}", exit_code=1)


@click.command("validate")
@click.argument("path", type=str, required=False)
@click.option(
    "--fail-at-end",
    help="If specified, only fail afterwards; allow all non-impacted projects to continue.",
    default=False,
    is_flag=True,
)
@flat_strategy_option
@flat_group_concurrency_option
def validate(
    path: str | None,
    fail_at_end: bool,
    strategy: str,
    group_concurrency: int | None,
) -> None:
    """Check whether the configuration is valid."""
    # Find all resources manifests
    paths = resource_dirs(path)
    quiet = strategy == "parallel"
    tasks: list[ResourceGroupTask] = [
        _ValidateTask(full_path, rel_path, quiet=quiet) for full_path, rel_path in paths
    ]

    results = execute_tasks(
        strategy,
        tasks,
        fail_at_end,
        waves=flat_wave(paths),
        group_concurrency=group_concurrency,
    )

    # Report any errors if fail_at_end has been enabled
    if any(r.status == "failed" for r in results):
        Log.fatal(
            "The syntax is probably incorrect in one of the projects. See above for errors."
        )
