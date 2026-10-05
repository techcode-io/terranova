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
import textwrap
from pathlib import Path

import pytest
from ruamel.yaml.comments import CommentedMap

from terranova.exceptions import InvalidManifestError, VersionManifestError
from terranova.migrations import (
    ADD_ENGINE_CHANGE,
    MIGRATION_RECIPES,
    EngineChoice,
    MigrationTo1_1,
    MigrationTo1_4,
    RecipeBook,
    migrate_manifest,
    recipes_from,
)
from terranova.schemas.manifest import LATEST_MANIFEST_VERSION, MANIFEST_SCHEMAS


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "manifest.yml"
    path.write_text(textwrap.dedent(body).lstrip())
    return path


V1_0 = """
    # keep me
    version: '1.0'

    metadata:
      name: Group  # inline
      description: ${DESC}
      url: https://example.com
      contact: mailto:a@example.com

    dependencies:
      - source: a.tf
        target: b.tf
"""

INVALID = """
    version: "1.0"
    metadata: {name: n}
    dependencies:
      - source: a.tf
"""


def _prompting(
    monkeypatch: pytest.MonkeyPatch, engine: EngineChoice | None
) -> list[int]:
    """Make `MigrationTo1_4` build its recipe with `engine`, counting prompts."""
    calls: list[int] = []

    def from_prompt(cls: type[MigrationTo1_4]) -> MigrationTo1_4:
        calls.append(1)
        return cls(engine=engine)

    monkeypatch.setattr(MigrationTo1_4, "from_prompt", classmethod(from_prompt))
    return calls


def test_latest_version_is_last_registered() -> None:
    assert LATEST_MANIFEST_VERSION == list(MANIFEST_SCHEMAS)[-1]


def test_one_recipe_per_minor_version() -> None:
    versions = list(MANIFEST_SCHEMAS)
    assert [r.source for r in MIGRATION_RECIPES] == versions[:-1]
    assert [r.target for r in MIGRATION_RECIPES] == versions[1:]


def test_every_version_has_a_recipe_chain_to_latest() -> None:
    for version in MANIFEST_SCHEMAS:
        chain = recipes_from(version)
        assert not chain if version == LATEST_MANIFEST_VERSION else chain
        if chain:
            assert chain[0].source == version
            assert chain[-1].target == LATEST_MANIFEST_VERSION


def test_no_recipe_for_unknown_version() -> None:
    with pytest.raises(VersionManifestError):
        recipes_from("0.1")


def test_base_recipe_only_bumps_version() -> None:
    data = CommentedMap(version="1.0", metadata={})
    assert MigrationTo1_1().apply(data) == ()
    assert data["version"] == "1.1"


def test_only_1_4_needs_a_prompt() -> None:
    assert [r for r in MIGRATION_RECIPES if r.needs_prompt({})] == [MigrationTo1_4]
    assert not MigrationTo1_4.needs_prompt({"engine": {}})


def test_recipe_without_questions_is_default_built() -> None:
    assert type(MigrationTo1_1.from_prompt()) is MigrationTo1_1


def test_recipe_book_prompts_once_per_recipe(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _prompting(monkeypatch, EngineChoice("opentofu", "latest"))
    book = RecipeBook()
    first = book.recipe_for(MigrationTo1_4, {})
    assert book.recipe_for(MigrationTo1_4, {}) is first
    assert calls == [1]


def test_recipe_book_skips_prompt_when_not_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _prompting(monkeypatch, None)
    recipe = RecipeBook().recipe_for(MigrationTo1_4, {"engine": {}})
    assert recipe == MigrationTo1_4() and calls == []


def test_bump_preserves_comments_and_adds_default_engine(tmp_path: Path) -> None:
    path = _write(tmp_path, V1_0)
    outcome = migrate_manifest(path, RecipeBook())
    text = path.read_text()
    assert outcome.migrated and outcome.engine_added
    assert outcome.changes == (ADD_ENGINE_CHANGE,)
    assert f'version: "{LATEST_MANIFEST_VERSION}"' in text
    assert "# keep me" in text and "# inline" in text
    assert "${DESC}" in text
    assert "\n\nengine:\n  name: terraform\n  version: system\n" in text
    assert text.index("metadata:") < text.index("dependencies:") < text.index("engine:")


def test_custom_engine_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _prompting(monkeypatch, EngineChoice("opentofu", "1.9.5"))
    path = _write(tmp_path, V1_0)
    migrate_manifest(path, RecipeBook())
    assert "name: opentofu" in path.read_text()
    assert "version: 1.9.5" in path.read_text()


def test_skip_engine_only_bumps_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = _prompting(monkeypatch, None)
    path = _write(tmp_path, V1_0)
    outcome = migrate_manifest(path, RecipeBook())
    assert outcome.migrated and not outcome.engine_added
    assert "engine" not in path.read_text()


def test_existing_engine_is_untouched_and_not_prompted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _prompting(monkeypatch, EngineChoice("terraform", "system"))
    path = _write(
        tmp_path,
        """
        version: "1.3"
        metadata: {name: n, description: d, url: u, contact: c}
        engine: {name: opentofu, version: latest}
        """,
    )
    migrate_manifest(path, RecipeBook())
    assert "name: opentofu" in path.read_text()
    assert calls == []


def test_up_to_date_is_not_rewritten(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        f"""
        version: "{LATEST_MANIFEST_VERSION}"
        metadata:   {{name: n, description: d, url: u, contact: c}}
        """,
    )
    before = path.read_text()
    assert not migrate_manifest(path, RecipeBook()).migrated
    assert path.read_text() == before


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    path = _write(tmp_path, V1_0)
    before = path.read_text()
    assert migrate_manifest(path, RecipeBook(), dry_run=True).migrated
    assert path.read_text() == before


def test_invalid_manifest_untouched(tmp_path: Path) -> None:
    path = _write(tmp_path, INVALID)
    before = path.read_text()
    with pytest.raises(InvalidManifestError):
        migrate_manifest(path, RecipeBook())
    assert path.read_text() == before


def test_missing_version_is_an_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "metadata: {name: n}\n")
    with pytest.raises(InvalidManifestError):
        migrate_manifest(path, RecipeBook())


def test_unknown_version_is_an_error(tmp_path: Path) -> None:
    path = _write(tmp_path, 'version: "9.9"\n')
    with pytest.raises(VersionManifestError):
        migrate_manifest(path, RecipeBook())
