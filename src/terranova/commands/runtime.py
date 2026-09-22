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

from terranova.commands.helpers import engine_versions_in_use
from terranova.engines import ENGINE_DESCRIPTORS, SYSTEM_VERSION, default_engine_manager
from terranova.exceptions import EngineError, EngineInUseError
from terranova.resources import ResourcesEngine
from terranova.utils import AppContext, log


def _format_size(size_bytes: int) -> str:
    """Human-readable size, e.g. `42.1 MB`."""
    size = float(size_bytes)
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


engine_filter_option = click.option(
    "--engine",
    type=click.Choice(list(ENGINE_DESCRIPTORS), case_sensitive=False),
    default=None,
    help="Restrict to one engine (`terraform` or `opentofu`). Defaults to every engine.",
)
"""Shared `--engine` filter for `runtime ls` - optional, lists every engine when unset."""

engine_option = click.option(
    "--engine",
    type=click.Choice(list(ENGINE_DESCRIPTORS), case_sensitive=False),
    default="terraform",
    help="Engine to operate on (`terraform` or `opentofu`).",
)
"""Shared `--engine` for `runtime install`/`rm`."""


@click.group("runtime")
def runtime() -> None:
    """Manage terraform/opentofu binaries cached under `~/.terranova/engines/`."""


@runtime.command("ls")
@engine_filter_option
@click.pass_obj
def runtime_ls(ctx: AppContext, engine: str | None) -> None:
    """List cached engine versions."""
    in_use = engine_versions_in_use(ctx)
    table = Table()
    table.add_column("Engine", style="cyan")
    table.add_column("Version", style="magenta")
    table.add_column("In use", justify="center")
    table.add_column("Size", justify="right")
    table.add_column("Path", style="dim")
    for item in default_engine_manager().list_installed(engine):
        used = (item.engine_name, item.version) in in_use
        table.add_row(
            item.engine_name,
            item.version,
            "[green]yes[/green]" if used else "",
            _format_size(item.size_bytes),
            str(item.path),
        )
    log.render(table)


@runtime.command("install")
@click.argument("version", type=str)
@engine_option
def runtime_install(version: str, engine: str) -> None:
    """Download, verify and cache VERSION, without needing a manifest. No-op if already cached."""
    if version == SYSTEM_VERSION:
        raise click.BadParameter(
            "`system` is never cached, nothing to install.", param_hint="'VERSION'"
        )
    log.action(f"Install {engine} {version}")
    try:
        binary = default_engine_manager().resolve(ResourcesEngine(engine, version))
    except EngineError as err:
        log.fatal(f"install {engine} {version}", err)
    log.success(f"install {engine} {version} at `{binary}`")


@runtime.command("rm")
@click.argument("version", type=str)
@engine_option
@click.option(
    "--force",
    is_flag=True,
    help="Remove even if a manifest under the conf dir still pins this version.",
)
@click.pass_obj
def runtime_rm(ctx: AppContext, version: str, engine: str, force: bool) -> None:
    """Remove one cached engine version."""
    if version == SYSTEM_VERSION:
        raise click.BadParameter(
            "`system` is never cached, nothing to remove.", param_hint="'VERSION'"
        )
    if not force and (engine, version) in engine_versions_in_use(ctx):
        log.fatal(f"remove {engine} {version}", EngineInUseError(engine, version))
    log.action(f"Remove {engine} {version}")
    try:
        default_engine_manager().remove(engine, version)
    except EngineError as err:
        log.fatal(f"remove {engine} {version}", err)
    log.success(f"remove {engine} {version}")


@runtime.command("prune")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be removed without deleting anything.",
)
@click.pass_obj
def runtime_prune(ctx: AppContext, dry_run: bool) -> None:
    """Remove every cached version not pinned by a manifest under the conf dir."""
    removed = default_engine_manager().prune(
        engine_versions_in_use(ctx), dry_run=dry_run
    )
    if not removed:
        log.action("Nothing to prune")
        return
    for item in removed:
        print(f"{item.engine_name} {item.version}")
    verb = "would remove" if dry_run else "remove"
    log.success(f"{verb} {len(removed)} version(s)")
