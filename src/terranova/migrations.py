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
"""Migration of `manifest.yml` files to the latest manifest version.

There is one `MigrationRecipe` per minor manifest version, each one moving a
manifest from the previous version to its own. A recipe is built from a prompt:
it asks the user whatever it needs to know (see `MigrationRecipe.from_prompt`),
once per run, however many manifests it is applied to.

When a new manifest version is released, add its recipe class and register it
in `MIGRATION_RECIPES`.
"""

import io
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Final, Self, override

import click
import yaml
from marshmallow import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from terranova.exceptions import (
    InvalidManifestError,
    MissingManifestError,
    UnreadableManifestError,
    VersionManifestError,
)
from terranova.io import write_atomic
from terranova.schemas.manifest import LATEST_MANIFEST_VERSION, MANIFEST_SCHEMAS

ADD_ENGINE_CHANGE: Final = "add `engine` section"
"""Change reported by `MigrationTo1_4` when it adds the `engine` section."""

_SKIP: Final = "skip"
"""Engine prompt answer meaning "don't add an `engine` section"."""

_PINNED: Final = "pinned"
"""Version prompt answer meaning "ask me for an exact version"."""

_EXACT_VERSION: Final = re.compile(r"^\d+\.\d+\.\d+$")
"""Exact engine version, like `1.9.5`."""


def _is_interactive() -> bool:
    """Whether the user can be prompted (stdin is a terminal)."""
    return sys.stdin.isatty()


def _exact_version(value: str) -> str:
    """Prompt value processor accepting only an exact version."""
    if not _EXACT_VERSION.match(value):
        raise click.BadParameter("Must be an exact version like `1.9.5`.")
    return value


@dataclass(frozen=True)
class EngineChoice:
    """Values of the `engine` section added to migrated manifests."""

    name: str = "terraform"
    version: str = "system"


class MigrationRecipe:
    """
    Moves a manifest from the previous minor version, `source`, to `target`.

    The base recipe only bumps `version`, which is all it takes when the new
    version is purely additive. A recipe needing more declares it by
    overriding `needs_prompt` and `from_prompt` to collect the user's choices,
    and `migrate` to apply them.
    """

    source: ClassVar[str]
    """Manifest version the recipe applies to."""

    target: ClassVar[str]
    """Manifest version the recipe produces."""

    @classmethod
    def needs_prompt(cls, data: dict[str, Any]) -> bool:  # pyright: ignore[reportExplicitAny]
        """
        Whether migrating this manifest requires the user's choices.

        Args:
            data: plain parsed manifest, as found before any recipe applied.
        """
        return False

    @classmethod
    def from_prompt(cls) -> Self:
        """
        Build the recipe by asking the user what it needs.

        Without an interactive terminal, nothing is asked and the defaults
        are used. Recipes with nothing to ask use the default construction.
        """
        return cls()

    def apply(self, data: CommentedMap) -> tuple[str, ...]:
        """
        Migrate `data` in place from `source` to `target`.

        Args:
            data: round-trip parsed manifest, so comments are preserved.

        Returns:
            description of the changes besides the version bump.
        """
        data["version"] = DoubleQuotedScalarString(self.target)
        return self.migrate(data)

    def migrate(self, data: CommentedMap) -> tuple[str, ...]:
        """Change the content beyond the version bump (nothing by default)."""
        return ()


class MigrationTo1_1(MigrationRecipe):
    """1.0 -> 1.1: adds `runbooks`, nothing to migrate."""

    source = "1.0"
    target = "1.1"


class MigrationTo1_2(MigrationRecipe):
    """1.1 -> 1.2: adds `imports`, nothing to migrate."""

    source = "1.1"
    target = "1.2"


class MigrationTo1_3(MigrationRecipe):
    """1.2 -> 1.3: adds the runbook env `if`, nothing to migrate."""

    source = "1.2"
    target = "1.3"


@dataclass(frozen=True)
class MigrationTo1_4(MigrationRecipe):
    """
    1.3 -> 1.4: adds the `engine` section.

    Attributes:
        engine: section to add to manifests lacking one, `None` to add none.
    """

    source = "1.3"
    target = "1.4"

    engine: EngineChoice | None = EngineChoice()

    @classmethod
    @override
    def needs_prompt(cls, data: dict[str, Any]) -> bool:  # pyright: ignore[reportExplicitAny]
        """Only manifests without an `engine` section need to choose one."""
        return "engine" not in data

    @classmethod
    @override
    def from_prompt(cls) -> Self:
        """Ask which engine and version to add, `skip` meaning none at all."""
        if not _is_interactive():
            return cls()
        click.echo("Some manifests predate the `engine` section, choose what to add.")
        name = click.prompt(
            "Engine",
            type=click.Choice(["terraform", "opentofu", _SKIP]),
            default="terraform",
        )
        if name == _SKIP:
            return cls(engine=None)
        version = click.prompt(
            "Engine version (`system` uses the binary found in PATH)",
            type=click.Choice(["system", "latest", _PINNED]),
            default="system",
        )
        if version == _PINNED:
            version = click.prompt("Exact version", value_proc=_exact_version)
        return cls(engine=EngineChoice(name, version))

    @override
    def migrate(self, data: CommentedMap) -> tuple[str, ...]:
        """Append the chosen `engine` section, unless one is already set."""
        if "engine" in data or self.engine is None:
            return ()
        data["engine"] = CommentedMap(
            name=self.engine.name, version=self.engine.version
        )
        data.yaml_set_comment_before_after_key("engine", before="\n")
        return (ADD_ENGINE_CHANGE,)


MIGRATION_RECIPES: Final[tuple[type[MigrationRecipe], ...]] = (
    MigrationTo1_1,
    MigrationTo1_2,
    MigrationTo1_3,
    MigrationTo1_4,
)
"""Registry of recipes, one per minor manifest version up to the latest."""


class RecipeBook:
    """
    Builds each recipe from a prompt at most once, whatever the manifest count.

    One book is used for a whole run, so the user is asked once per recipe
    and the answers apply to every manifest migrated.
    """

    def __init__(self) -> None:
        """Init an empty book."""
        self.__built: dict[type[MigrationRecipe], MigrationRecipe] = {}

    def recipe_for(
        self,
        recipe_type: type[MigrationRecipe],
        data: dict[str, Any],  # pyright: ignore[reportExplicitAny]
    ) -> MigrationRecipe:
        """
        Get the recipe to apply to a manifest.

        Args:
            recipe_type: the recipe class to apply.
            data: plain parsed manifest, as found before any recipe applied.

        Returns:
            the recipe built from the prompt when the manifest needs the
            user's choices, the default one otherwise.
        """
        if not recipe_type.needs_prompt(data):
            return recipe_type()
        if recipe_type not in self.__built:
            self.__built[recipe_type] = recipe_type.from_prompt()
        return self.__built[recipe_type]


@dataclass(frozen=True)
class MigrationOutcome:
    """Result of migrating one manifest."""

    from_version: str
    to_version: str
    changes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def migrated(self) -> bool:
        """Whether the manifest has been (or would be) rewritten."""
        return self.from_version != self.to_version

    @property
    def engine_added(self) -> bool:
        """Whether an `engine` section has been (or would be) added."""
        return ADD_ENGINE_CHANGE in self.changes


def recipes_from(version: str) -> list[type[MigrationRecipe]]:
    """
    Chain the recipes leading from `version` to the latest manifest version.

    Args:
        version: current manifest version.

    Returns:
        ordered recipe classes to apply, empty when already at the latest.

    Raises:
        VersionManifestError: no recipe leaves from `version` (or from an
            intermediate one), so the chain can't reach the latest version.
    """
    by_source = {recipe.source: recipe for recipe in MIGRATION_RECIPES}
    chain: list[type[MigrationRecipe]] = []
    while version != LATEST_MANIFEST_VERSION:
        recipe = by_source.get(version)
        if recipe is None:
            raise VersionManifestError(version)
        chain.append(recipe)
        version = recipe.target
    return chain


def _read_yaml(path: Path) -> tuple[str, dict[str, Any]]:  # pyright: ignore[reportExplicitAny]
    """Read a manifest as raw text and as a plain mapping carrying a `version`."""
    if not path.is_file():
        raise MissingManifestError(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise UnreadableManifestError(path) from err
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise InvalidManifestError(path) from err
    if not isinstance(data, dict) or "version" not in data:
        raise InvalidManifestError(path)
    return text, data


def _validate(path: Path, version: str, data: dict[str, Any]) -> None:  # pyright: ignore[reportExplicitAny]
    """Validate `data` against the schema of manifest `version`."""
    schema_cls = MANIFEST_SCHEMAS.get(version)
    if schema_cls is None:
        raise VersionManifestError(version)
    try:
        schema_cls().load(data)
    except ValidationError as err:
        raise InvalidManifestError(path) from err


def _round_trip_yaml() -> YAML:
    """Build a YAML round-tripper keeping quotes and avoiding line rewrapping."""
    yml = YAML(typ="rt")
    yml.preserve_quotes = True
    yml.indent(mapping=2, sequence=4, offset=2)
    yml.width = 4096
    return yml


def migrate_manifest(
    path: Path, book: RecipeBook, dry_run: bool = False
) -> MigrationOutcome:
    """
    Migrate a manifest file to the latest manifest version, in place.

    Comments, key order and quoting are preserved. The manifest is validated
    against its current schema before, and against the latest schema after,
    the rewrite. Nothing is written unless both checks pass.

    Args:
        path: the `manifest.yml` file.
        book: builds the recipes, prompting the user when one needs it.
        dry_run: compute the outcome without writing.

    Returns:
        what happened, `migrated` being false when already up to date.

    Raises:
        ManifestError: the manifest can't be read, is invalid or has an
            unsupported version. The file is left untouched.
    """
    text, plain = _read_yaml(path)
    version = str(plain["version"])
    _validate(path, version, plain)
    chain = recipes_from(version)
    if not chain:
        return MigrationOutcome(version, version)

    yml = _round_trip_yaml()
    data: CommentedMap = yml.load(text)
    changes: list[str] = []
    for recipe_type in chain:
        changes.extend(book.recipe_for(recipe_type, plain).apply(data))

    out = io.StringIO()
    yml.dump(data, out)
    new_text = out.getvalue()
    _validate(path, LATEST_MANIFEST_VERSION, yaml.safe_load(new_text))

    if not dry_run:
        write_atomic(path, new_text)
    return MigrationOutcome(version, LATEST_MANIFEST_VERSION, tuple(changes))
