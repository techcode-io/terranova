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
CLI entry point (`terranova.cli:main`, see `pyproject.toml`'s `[project.scripts]`).

Command modules build a plain `click.Command` via `@click.command(...)` and
don't import `main` themselves - that would make every command module
depend on this one while this module also depends on all of them, a
circular dependency. Instead, `main` is defined first, then each command is
imported and registered explicitly below with `main.add_command(...)`, so
this module stays the only one that knows about the group.
"""

from pathlib import Path

import click

from . import __version__
from .utils import SharedContext


@click.group("terranova")
@click.option("--debug", help="Enable debug mode.", is_flag=True, default=False)
@click.option(
    "-v",
    "--verbose",
    help="Make the operation more talkative.",
    is_flag=True,
    default=False,
)
@click.option(
    "--conf-dir",
    help="Conf directory path.",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    envvar="TERRANOVA_CONF_DIR",
    default="./conf",
)
@click.version_option(__version__)
def main(debug: bool, verbose: bool, conf_dir: Path) -> None:
    """Terranova is a thin wrapper for Terraform that provides extra tools and logic to handle Terraform configurations at scale."""
    SharedContext.init(debug, verbose, conf_dir)


from .commands.apply import apply
from .commands.define import define
from .commands.destroy import destroy
from .commands.docs import docs
from .commands.fmt import fmt
from .commands.get import get
from .commands.graph import graph
from .commands.init import init
from .commands.ls import ls
from .commands.output import output
from .commands.plan import plan
from .commands.runbook import runbook
from .commands.taint import taint
from .commands.untaint import untaint
from .commands.validate import validate

for command in (
    apply,
    define,
    destroy,
    docs,
    fmt,
    get,
    graph,
    init,
    ls,
    output,
    plan,
    runbook,
    taint,
    untaint,
    validate,
):
    main.add_command(command)

__all__ = [
    "apply",
    "define",
    "destroy",
    "docs",
    "fmt",
    "get",
    "graph",
    "init",
    "ls",
    "main",
    "output",
    "plan",
    "runbook",
    "taint",
    "untaint",
    "validate",
]
