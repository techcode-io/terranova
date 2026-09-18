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

from terranova.commands.helpers import (
    auto_scope_option,
    mount_context,
    resolve_resource_dirs,
)
from terranova.process import ErrorReturnCode
from terranova.utils import AppContext, log


@click.command("destroy")
@click.argument("path", type=str, required=False)
@auto_scope_option
@click.pass_obj
def destroy(ctx: AppContext, path: str | None, auto_scope: bool) -> None:
    """Destroy previously-created resources."""
    # Find all resources manifests
    paths = resolve_resource_dirs(ctx.conf_dir, ctx.resources_dir, path, auto_scope)

    # Format all paths
    for full_path, rel_path in paths:
        log.action(f"Destroying resources: {rel_path}")

        # Mount terraform context
        terraform = mount_context(
            full_path,
            ctx.resources_dir,
            ctx.terraform_shared_plugin_cache_dir,
            ctx.verbose,
            import_vars=True,
        )

        # Execute destroy command
        try:
            terraform.destroy()
        except ErrorReturnCode as err:
            raise Exit(code=err.exit_code) from err
