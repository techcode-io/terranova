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
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import cast

import click
import mdformat
from jinja2 import Environment, PackageLoader

from terranova.commands.helpers import (
    auto_scope_option,
    discover_resources,
    read_manifest,
    resolve_resource_dirs,
)
from terranova.utils import AppContext, Constants


def format_markdown(text: str) -> str:
    """Format markdown text, shielding callers from mdformat's untyped signature."""
    text_fn = cast("Callable[[str], str]", mdformat.text)
    return text_fn(text)


@click.command("docs")
@click.argument("path", type=str, required=False)
@auto_scope_option
@click.option(
    "--docs-dir",
    help="Docs directory path.",
    type=click.Path(path_type=Path),
    required=True,
    default="./docs",
)
@click.pass_obj
def docs(ctx: AppContext, path: str | None, auto_scope: bool, docs_dir: Path) -> None:
    """Generate documentation for all resources."""
    # Find all resources manifests
    paths = resolve_resource_dirs(ctx.conf_dir, ctx.resources_dir, path, auto_scope)
    jobs: list[tuple[Path, Path]] = [
        (full_path, docs_dir.joinpath(rel_path)) for full_path, rel_path in paths
    ]

    # Clean docs dir, unless scoped - a scoped run must not delete docs for
    # resource groups outside its scope.
    if not path and not auto_scope and docs_dir.exists():
        shutil.rmtree(docs_dir.as_posix())

    # Generate docs
    env = Environment(loader=PackageLoader("terranova", "templates"))
    tmpl = env.get_template("resources.md")
    for resources_path, target_path in jobs:
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Read resources manifest and find all resources
        manifest = read_manifest(resources_path)
        resources = discover_resources(resources_path)

        # Write documentation file
        rendering = tmpl.render({"manifest": manifest, "resources": resources})
        formatted = format_markdown(rendering)
        target_path.with_suffix(".md").write_text(
            data=formatted, encoding=Constants.ENCODING_UTF_8
        )
