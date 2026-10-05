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
    complete_resource_path,
    resolve_resource_dirs,
)
from terranova.exceptions import ManifestError
from terranova.migrations import RecipeBook, migrate_manifest
from terranova.schemas.manifest import LATEST_MANIFEST_VERSION
from terranova.utils import AppContext, Constants, log


@click.command("migrate")
@click.argument("path", type=str, required=False, shell_complete=complete_resource_path)
@auto_scope_option
@click.option(
    "--dry-run",
    help="Report what would change without writing any manifest.",
    is_flag=True,
    default=False,
)
@click.pass_obj
def migrate(ctx: AppContext, path: str | None, auto_scope: bool, dry_run: bool) -> None:
    """Migrate manifests to the latest manifest version in batch."""
    dirs = resolve_resource_dirs(ctx.conf_dir, ctx.resources_dir, path, auto_scope)

    # Recipes prompt on first use only, then the answers apply to every manifest.
    book = RecipeBook()

    migrated = up_to_date = 0
    failed: list[str] = []
    for full_path, rel_path in sorted(dirs, key=lambda entry: entry[1]):
        manifest = full_path / Constants.MANIFEST_FILE_NAME
        try:
            outcome = migrate_manifest(manifest, book, dry_run)
        except ManifestError as err:
            log.failure(f"migrate `{rel_path}`", err)
            failed.append(rel_path)
            continue
        if not outcome.migrated:
            up_to_date += 1
            continue
        migrated += 1
        verb = "Would migrate" if dry_run else "Migrated"
        extra = f" ({', '.join(outcome.changes)})" if outcome.changes else ""
        log.action(
            f"{verb}: {rel_path} {outcome.from_version} -> {outcome.to_version}{extra}"
        )

    log.action(
        f"{migrated} {'to migrate' if dry_run else 'migrated'}, "
        f"{up_to_date} already at {LATEST_MANIFEST_VERSION}, {len(failed)} failed"
    )
    if failed:
        raise Exit(code=1)
