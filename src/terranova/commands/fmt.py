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
from click.exceptions import Exit

from terranova.commands.helpers import (
    execute_tasks,
    flat_group_concurrency_option,
    flat_strategy_option,
    flat_wave,
    mount_context,
    resource_dirs,
)
from terranova.executor import ResourceGroupTask
from terranova.utils import AppContext


class _FmtTask(ResourceGroupTask):
    """Formats one project's configuration."""

    @override
    def run(self) -> None:
        if not self.quiet:
            self.ctx.log.action(f"Formatting: {self.rel_path}")
        terraform = mount_context(self.ctx, self.full_path)
        terraform.fmt()


@click.command("fmt")
@click.argument("path", type=str, required=False)
@flat_strategy_option
@flat_group_concurrency_option
@click.pass_obj
def fmt(
    ctx: AppContext, path: str | None, strategy: str, group_concurrency: int | None
) -> None:
    """Reformat your configuration in the standard style."""
    # Find all resources manifests
    paths = resource_dirs(ctx, path)
    quiet = strategy == "parallel"
    tasks: list[ResourceGroupTask] = [
        _FmtTask(ctx, full_path, rel_path, quiet=quiet) for full_path, rel_path in paths
    ]

    results = execute_tasks(
        ctx,
        strategy,
        tasks,
        fail_at_end=False,
        waves=flat_wave(paths),
        group_concurrency=group_concurrency,
    )

    failures = [r for r in results if r.status == "failed"]
    if failures:
        raise Exit(code=failures[0].exit_code or 1)
