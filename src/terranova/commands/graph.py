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
"""
The `terranova graph` command - wraps `terraform graph` for one resource group.

Not to be confused with `terranova.graph`, the dependency-graph module used by
`plan`/`apply` to compute execution waves from manifest `imports`.
"""

import click
from click.exceptions import Exit

from terranova.commands.helpers import mount_context
from terranova.process import ErrorReturnCode
from terranova.utils import AppContext


@click.command("graph")
@click.argument("path", type=str)
@click.pass_obj
def graph(ctx: AppContext, path: str) -> None:
    """Generate a Graphviz graph of the steps in an operation."""
    # Construct resources path
    full_path = ctx.resources_dir.joinpath(path)

    # Mount terraform context
    terraform = mount_context(ctx, full_path)

    # Execute destroy command
    try:
        terraform.graph()
    except ErrorReturnCode as err:
        raise Exit(code=err.exit_code) from err
