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
import click
from rich.table import Table

from terranova.commands.helpers import SelectorType, discover_resources, resource_dirs
from terranova.resources import Selector
from terranova.utils import AppContext, log


@click.command("get")
@click.argument("path", type=str, required=False)
@click.option(
    "--selector", "selectors", type=SelectorType(), required=False, multiple=True
)
@click.pass_obj
def get(ctx: AppContext, path: str | None, selectors: list[Selector] | None) -> None:
    """Display one or many resources."""
    # Find all resources manifests
    paths = resource_dirs(ctx.resources_dir, path)

    # Render resources table
    table = Table()
    table.add_column("Path", justify="left", style="cyan", no_wrap=True)
    table.add_column("Type", justify="left", style="green")
    table.add_column("Name", style="magenta")
    for full_path, rel_path in paths:
        resources = discover_resources(full_path, selectors)
        for resource in resources:
            table.add_row(rel_path, resource.type, resource.name)
    log.render(table)
