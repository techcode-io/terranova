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
import os

import click
from click.exceptions import Exit

from terranova.commands.helpers import mount_context, read_manifest, resource_dirs
from terranova.process import ErrorReturnCode
from terranova.utils import Log, SharedContext


@click.command("init")
@click.argument("path", type=str, required=False)
@click.option(
    "--migrate-state",
    help="Reconfigure a backend, and attempt to migrate any existing state.",
    is_flag=True,
)
@click.option(
    "--no-backend",
    help="Disable backend for this configuration and use what was previously instead.",
    is_flag=True,
)
@click.option(
    "--reconfigure",
    help="Reconfigure a backend, ignoring any saved configuration.",
    is_flag=True,
)
@click.option(
    "--upgrade", help="Install the latest module and provider versions.", is_flag=True
)
@click.option(
    "--fail-at-end",
    help="If specified, only fail afterwards; allow all non-impacted projects to continue.",
    default=False,
    is_flag=True,
)
def init(
    path: str | None,
    migrate_state: bool,
    no_backend: bool,
    reconfigure: bool,
    upgrade: bool,
    fail_at_end: bool,
) -> None:
    """Init resources manifest."""
    # Find all resources manifests
    paths = resource_dirs(path)

    # Store errors if fail_at_end
    errors = False

    # Init all paths
    for full_path, rel_path in paths:
        Log.action(f"Initializing: {rel_path}")

        # Ensure manifest exists and can be read
        manifest = read_manifest(full_path)

        # Remove all symbolic links
        symbolic_links = [file for file in full_path.iterdir() if file.is_symlink()]
        for link in symbolic_links:
            os.unlink(link.as_posix())

        # Save workdir
        cwd = os.getcwd()
        try:
            # Switch to resources dir
            os.chdir(full_path.as_posix())

            # Create new symbolic links
            if manifest.dependencies:
                for dependency in manifest.dependencies:
                    try:
                        # Ensure parent directories exist
                        target_dirname = os.path.dirname(dependency.target)
                        if target_dirname:
                            os.makedirs(target_dirname, exist_ok=True)

                        os.symlink(
                            os.path.relpath(
                                SharedContext.shared_dir()
                                .joinpath(dependency.source)
                                .as_posix(),
                                full_path.joinpath(target_dirname).as_posix(),
                            ),
                            dependency.target,
                        )
                    except FileExistsError:
                        # The symlink already exists and it's probably fine
                        pass
        finally:
            os.chdir(cwd)

        # Cleanup various directories
        for dirname in ["outputs", "templates", "runbooks"]:
            dir_path = full_path / dirname
            try:
                # Is it empty
                if (
                    dir_path.exists()
                    and dir_path.is_dir()
                    and not any(dir_path.iterdir())
                ):
                    dir_path.rmdir()
            except OSError:
                Log.fatal(f"delete the directory at: {dir_path.as_posix()}")

        try:
            # Mount terraform context
            terraform = mount_context(full_path, manifest)
            terraform.init(
                backend_config={
                    "key": os.path.relpath(full_path, SharedContext.resources_dir())
                },
                migrate_state=migrate_state,
                no_backend=no_backend,
                reconfigure=reconfigure,
                upgrade=upgrade,
            )
        except ErrorReturnCode:
            errors = True
            if not fail_at_end:
                break

    # Report any errors if fail_at_end has been enabled
    if errors:
        raise Exit(code=1)
