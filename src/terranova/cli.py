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
don't import `main` themselves, so importing them here doesn't create a
circular dependency; each one is registered on `main` below with
`main.add_command(...)`.
"""

from pathlib import Path

import click

from terranova import __version__
from terranova.commands.apply import apply
from terranova.commands.define import define
from terranova.commands.destroy import destroy
from terranova.commands.docs import docs
from terranova.commands.fmt import fmt
from terranova.commands.get import get
from terranova.commands.graph import graph
from terranova.commands.init import init
from terranova.commands.ls import ls
from terranova.commands.output import output
from terranova.commands.plan import plan
from terranova.commands.runbook import runbook
from terranova.commands.taint import taint
from terranova.commands.untaint import untaint
from terranova.commands.validate import validate
from terranova.utils import AppContext, log


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
@click.pass_context
def main(ctx: click.Context, debug: bool, verbose: bool, conf_dir: Path) -> None:
    """Terranova is a thin wrapper for Terraform that provides extra tools and logic to handle Terraform configurations at scale."""
    log.configure(debug)
    ctx.obj = AppContext(conf_dir=conf_dir, verbose=verbose)


main.add_command(apply)
main.add_command(define)
main.add_command(destroy)
main.add_command(docs)
main.add_command(fmt)
main.add_command(get)
main.add_command(graph)
main.add_command(init)
main.add_command(ls)
main.add_command(output)
main.add_command(plan)
main.add_command(runbook)
main.add_command(taint)
main.add_command(untaint)
main.add_command(validate)

__all__ = ["main"]
