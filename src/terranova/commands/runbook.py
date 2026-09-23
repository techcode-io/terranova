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
    complete_resource_path,
    complete_runbook_name,
    extract_import_vars,
    read_manifest,
)
from terranova.engines import default_engine_manager
from terranova.exceptions import (
    AmbiguousRunbookError,
    EngineError,
    MissingRunbookEnvError,
    MissingRunbookError,
    SelfImportNotReadyError,
)
from terranova.graph import normalize_rel_path
from terranova.process import ErrorReturnCode
from terranova.utils import AppContext, log


@click.command("runbook")
@click.argument("path", type=str, shell_complete=complete_resource_path)
@click.argument("name", type=str, shell_complete=complete_runbook_name)
@click.pass_obj
def runbook(ctx: AppContext, path: str, name: str) -> None:
    """Execute a runbook."""
    # Construct resources path
    full_path = ctx.resources_dir.joinpath(path)

    # Ensure manifest exists and can be read
    manifest = read_manifest(full_path)

    # Extract runbook
    matching_runbooks = (
        [rb for rb in manifest.runbooks if rb.name == name] if manifest.runbooks else []
    )
    if not matching_runbooks:
        log.fatal("execute runbook", MissingRunbookError(name))
    if len(matching_runbooks) > 1:
        log.fatal("execute runbook", AmbiguousRunbookError(name))

    # Import vars - a runbook runs after its own resource group's apply, so
    # unlike `plan`/`apply`/etc. it can resolve a self-import from that
    # resulting state instead of skipping it.
    try:
        import_vars = extract_import_vars(
            manifest,
            ctx.resources_dir,
            ctx.terraform_shared_plugin_cache_dir,
            ctx.verbose,
            self_rel_path=normalize_rel_path(path),
            resolve_self=True,
        )
    except SelfImportNotReadyError as err:
        log.fatal("resolve self-import", err)

    # Resolve the pinned engine so runbooks see the same binary as the group
    engine_name = manifest.engine.name if manifest.engine else "terraform"
    try:
        binary = default_engine_manager().resolve(manifest.engine)
    except EngineError as err:
        log.fatal(f"resolve {engine_name} engine", err)

    # Execute runbook
    executable_runbook = next(iter(matching_runbooks))
    try:
        executable_runbook.exec(
            ctx.conf_dir,
            path,
            full_path / "runbooks",
            import_vars,
            engine_dir=binary.parent if binary else None,
        )
    except MissingRunbookEnvError as err:
        log.fatal("find environment variable", err)
    except ErrorReturnCode as err:
        raise Exit(code=err.exit_code) from err
