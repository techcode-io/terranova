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

from terranova.commands.helpers import mount_context
from terranova.process import ErrorReturnCode
from terranova.utils import SharedContext


@click.command("untaint")
@click.argument("path", type=str)
@click.argument("address", type=str)
def untaint(path: str, address: str) -> None:
    """Remove the 'tainted' state from a resource instance."""
    # Construct resources path
    full_path = SharedContext.resources_dir().joinpath(path)

    # Mount terraform context
    terraform = mount_context(full_path, import_vars=True)

    # Execute untaint command
    try:
        terraform.untaint(address)
    except ErrorReturnCode as err:
        raise Exit(code=err.exit_code) from err
