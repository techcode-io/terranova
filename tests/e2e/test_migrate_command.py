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
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from terranova.cli import main
from terranova.schemas.manifest import LATEST_MANIFEST_VERSION

MANIFEST = """\
version: "{version}"
metadata:
  name: {name}
  description: d
  url: https://example.com
  contact: mailto:a@example.com
"""


def _group(
    conf: Path, name: str, version: str = "1.0", body: str | None = None
) -> Path:
    path = conf / "resources" / name / "manifest.yml"
    path.parent.mkdir(parents=True)
    path.write_text(body or MANIFEST.format(version=version, name=name))
    return path


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("terranova.migrations._is_interactive", lambda: True)


def _run(
    runner: CliRunner, conf: Path, *args: str, user_input: str | None = None
) -> Result:
    return runner.invoke(
        main, args=["--conf-dir", str(conf), "migrate", *args], input=user_input
    )


def test_migrates_all_manifests_with_defaults(
    runner: CliRunner, tmp_path: Path
) -> None:
    a, b = _group(tmp_path, "a"), _group(tmp_path, "b", "1.2")
    result = _run(runner, tmp_path)
    assert result.exit_code == 0, result.output
    for manifest in (a, b):
        text = manifest.read_text()
        assert f'version: "{LATEST_MANIFEST_VERSION}"' in text
        assert "name: terraform" in text and "version: system" in text
    assert "2 migrated" in result.output


def test_second_run_is_idempotent(runner: CliRunner, tmp_path: Path) -> None:
    manifest = _group(tmp_path, "a")
    _run(runner, tmp_path)
    after_first = manifest.read_text()
    result = _run(runner, tmp_path)
    assert result.exit_code == 0
    assert manifest.read_text() == after_first
    assert "0 migrated, 1 already" in result.output


def test_dry_run_leaves_files_unchanged(runner: CliRunner, tmp_path: Path) -> None:
    manifest = _group(tmp_path, "a")
    before = manifest.read_text()
    result = _run(runner, tmp_path, "--dry-run")
    assert result.exit_code == 0
    assert manifest.read_text() == before
    assert "Would migrate" in result.output


def test_path_scopes_the_migration(runner: CliRunner, tmp_path: Path) -> None:
    a, b = _group(tmp_path, "a"), _group(tmp_path, "b")
    before_b = b.read_text()
    assert _run(runner, tmp_path, "a").exit_code == 0
    assert LATEST_MANIFEST_VERSION in a.read_text()
    assert b.read_text() == before_b


def test_invalid_manifest_fails_but_others_migrate(
    runner: CliRunner, tmp_path: Path
) -> None:
    bad = _group(
        tmp_path, "bad", body='version: "1.0"\ndependencies:\n  - source: a.tf\n'
    )
    good = _group(tmp_path, "good")
    bad_before = bad.read_text()
    result = _run(runner, tmp_path)
    assert result.exit_code == 1
    assert bad.read_text() == bad_before
    assert LATEST_MANIFEST_VERSION in good.read_text()


def test_prompt_picks_engine_once_for_all(
    runner: CliRunner, tmp_path: Path, interactive: None
) -> None:
    _ = interactive
    a, b = _group(tmp_path, "a"), _group(tmp_path, "b")
    result = _run(runner, tmp_path, user_input="opentofu\nlatest\n")
    assert result.exit_code == 0, result.output
    for manifest in (a, b):
        assert "name: opentofu" in manifest.read_text()
        assert "version: latest" in manifest.read_text()
    assert result.output.count("Engine (") == 1


def test_prompt_pinned_version_reprompts_until_valid(
    runner: CliRunner, tmp_path: Path, interactive: None
) -> None:
    _ = interactive
    a = _group(tmp_path, "a")
    result = _run(runner, tmp_path, user_input="terraform\npinned\nnope\n1.9.5\n")
    assert result.exit_code == 0, result.output
    assert "version: 1.9.5" in a.read_text()


def test_prompt_skip_adds_no_engine(
    runner: CliRunner, tmp_path: Path, interactive: None
) -> None:
    _ = interactive
    a = _group(tmp_path, "a")
    assert _run(runner, tmp_path, user_input="skip\n").exit_code == 0
    assert "engine" not in a.read_text()


def test_prompt_not_shown_when_nothing_needs_engine(
    runner: CliRunner, tmp_path: Path, interactive: None
) -> None:
    _ = interactive
    _group(tmp_path, "a", LATEST_MANIFEST_VERSION)
    result = _run(runner, tmp_path)
    assert result.exit_code == 0
    assert "Engine" not in result.output
