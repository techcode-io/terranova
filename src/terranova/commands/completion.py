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
from click.shell_completion import get_completion_class

PROG_NAME = "terranova"
COMPLETE_VAR = "_TERRANOVA_COMPLETE"


@click.command("completion")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
@click.pass_context
def completion(ctx: click.Context, shell: str) -> None:
    """Print the shell completion script for bash, zsh or fish."""
    comp_cls = get_completion_class(shell)
    if comp_cls is None:  # pragma: no cover - guarded by click.Choice
        raise click.BadParameter(f"unsupported shell `{shell}`")
    click.echo(comp_cls(ctx.find_root().command, {}, PROG_NAME, COMPLETE_VAR).source())
