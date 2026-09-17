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

from terranova.commands.helpers import extract_import_vars, read_manifest
from terranova.exceptions import (
    AmbiguousRunbookError,
    MissingRunbookEnvError,
    MissingRunbookError,
)
from terranova.process import ErrorReturnCode
from terranova.utils import AppContext


@click.command("runbook")
@click.argument("path", type=str)
@click.argument("name", type=str)
@click.pass_obj
def runbook(ctx: AppContext, path: str, name: str) -> None:
    """Execute a runbook."""
    # Construct resources path
    full_path = ctx.resources_dir.joinpath(path)

    # Ensure manifest exists and can be read
    manifest = read_manifest(ctx, full_path)

    # Extract runbook
    matching_runbooks = (
        [rb for rb in manifest.runbooks if rb.name == name] if manifest.runbooks else []
    )
    if not matching_runbooks:
        ctx.log.fatal("execute runbook", MissingRunbookError(name))
    if len(matching_runbooks) > 1:
        ctx.log.fatal("execute runbook", AmbiguousRunbookError(name))

    # Import vars
    import_vars = extract_import_vars(ctx, manifest)

    # Execute runbook
    executable_runbook = next(iter(matching_runbooks))
    try:
        executable_runbook.exec(ctx, path, full_path / "runbooks", import_vars)
    except MissingRunbookEnvError as err:
        ctx.log.fatal("find environment variable", err)
    except ErrorReturnCode as err:
        raise Exit(code=err.exit_code) from err
