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
from click.shell_completion import ShellComplete
from click.testing import CliRunner

from terranova.cli import main

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _complete(conf_dir: Path | str, args: list[str], incomplete: str) -> list[str]:
    comp = ShellComplete(main, {}, "terranova", "_TERRANOVA_COMPLETE")
    return [
        item.value
        for item in comp.get_completions(
            ["--conf-dir", str(conf_dir), *args], incomplete
        )
    ]


def test_resource_path_lists_groups() -> None:
    assert _complete(FIXTURES / "runbook_with_is_defined", ["plan"], "") == [
        "resource_group"
    ]


def test_resource_path_filters_by_prefix() -> None:
    assert _complete(FIXTURES / "runbook_with_is_defined", ["plan"], "zzz") == []


def test_resource_path_missing_conf_dir(tmp_path: Path) -> None:
    assert _complete(tmp_path / "missing", ["plan"], "") == []


def test_runbook_name() -> None:
    conf = FIXTURES / "runbook_with_is_defined"
    assert _complete(conf, ["runbook", "resource_group"], "") == ["check-optional-env"]
    assert _complete(conf, ["runbook", "resource_group"], "x") == []


def test_runbook_name_unknown_group() -> None:
    conf = FIXTURES / "runbook_with_is_defined"
    assert _complete(conf, ["runbook", "nope"], "") == []


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_completion_command(shell: str) -> None:
    result = CliRunner().invoke(main, ["completion", shell])
    assert result.exit_code == 0, result.output
    assert "_TERRANOVA_COMPLETE" in result.output


def test_completion_command_rejects_unknown_shell() -> None:
    result = CliRunner().invoke(main, ["completion", "powershell"])
    assert result.exit_code == 2


def test_missing_conf_dir_still_errors(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["--conf-dir", str(tmp_path / "x"), "ls"])
    assert result.exit_code == 2
    assert "does not exist" in result.output
