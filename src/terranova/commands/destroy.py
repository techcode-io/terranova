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
from click.exceptions import Exit

from terranova.commands.helpers import mount_context, resource_dirs
from terranova.process import ErrorReturnCode
from terranova.utils import AppContext


@click.command("destroy")
@click.argument("path", type=str, required=False)
@click.pass_obj
def destroy(ctx: AppContext, path: str | None) -> None:
    """Destroy previously-created resources."""
    # Find all resources manifests
    paths = resource_dirs(ctx, path)

    # Format all paths
    for full_path, rel_path in paths:
        ctx.log.action(f"Destroying resources: {rel_path}")

        # Mount terraform context
        terraform = mount_context(ctx, full_path, import_vars=True)

        # Execute destroy command
        try:
            terraform.destroy()
        except ErrorReturnCode as err:
            raise Exit(code=err.exit_code) from err
