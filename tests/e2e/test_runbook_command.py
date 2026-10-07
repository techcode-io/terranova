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
import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from terranova.cli import main
from tests.e2e.conftest import assert_result

_MANIFEST_HEADER = """\
version: "1.3"

metadata:
  name: Runbook Test
  description: Test resources for the runbook command
  url: https://github.com/techcode-io/terranova
  contact: mailto:adrien.mannocci@gmail.com

runbooks:
"""


def _runbook_conf_dir(root: Path, runbooks: str, scripts: dict[str, str]) -> Path:
    """
    Build a conf dir whose runbooks run `scripts` through the current interpreter.

    Entrypoints are python scripts rather than shell ones, so they run on every OS.
    """
    group_dir = root / "resources" / "resource_group"
    runbooks_dir = group_dir / "runbooks"
    runbooks_dir.mkdir(parents=True)
    for name, body in scripts.items():
        (runbooks_dir / name).write_text(body)
    entrypoint = json.dumps(sys.executable)  # a JSON string is a valid YAML scalar
    (group_dir / "manifest.yml").write_text(
        _MANIFEST_HEADER + runbooks.replace("{python}", entrypoint)
    )
    return root


@pytest.fixture
def errors_conf_dir(tmp_path: Path) -> Path:
    return _runbook_conf_dir(
        tmp_path,
        """\
  - name: duplicate
    entrypoint: {python}
    args: [ok.py]
  - name: duplicate
    entrypoint: {python}
    args: [ok.py]
  - name: needs-env
    entrypoint: {python}
    args: [ok.py]
    env:
      - name: REQUIRED_VAR
  - name: fails
    entrypoint: {python}
    args: [fail.py]
""",
        {
            "ok.py": 'print("ok")\n',
            "fail.py": 'import sys\nprint("failing", file=sys.stderr)\nsys.exit(3)\n',
        },
    )


@pytest.fixture
def is_defined_conf_dir(tmp_path: Path) -> Path:
    return _runbook_conf_dir(
        tmp_path,
        """\
  - name: check-optional-env
    entrypoint: {python}
    args: [runbook.py]
    env:
      - name: OPTIONAL_VAR
        if: is_defined
""",
        {
            "runbook.py": (
                "import os\n"
                'print("OPTIONAL_VAR=" + os.environ.get("OPTIONAL_VAR", ""))\n'
            )
        },
    )


def test_runbook_with_env_if_is_defined_when_var_is_not_set(
    runner: CliRunner, is_defined_conf_dir: Path
) -> None:
    """Test that runbook with if: is_defined condition succeeds when env var is not set."""
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(is_defined_conf_dir),
            "runbook",
            "resource_group",
            "check-optional-env",
        ],
    )
    stdout, _ = assert_result(result)
    assert "OPTIONAL_VAR=" in stdout


def test_runbook_with_env_if_is_defined_when_var_is_set(
    runner: CliRunner, is_defined_conf_dir: Path
) -> None:
    """Test that runbook with if: is_defined condition receives env var when set."""
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(is_defined_conf_dir),
            "runbook",
            "resource_group",
            "check-optional-env",
        ],
        env={"OPTIONAL_VAR": "test_value"},
    )
    stdout, _ = assert_result(result)
    assert "OPTIONAL_VAR=test_value" in stdout


def test_runbook_missing_name_fails(runner: CliRunner, errors_conf_dir: Path) -> None:
    """Test that requesting an undefined runbook name reports a fatal error."""
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(errors_conf_dir),
            "runbook",
            "resource_group",
            "does-not-exist",
        ],
    )
    assert result.exit_code == 1
    assert "isn't defined" in result.stderr


def test_runbook_ambiguous_name_fails(runner: CliRunner, errors_conf_dir: Path) -> None:
    """Test that a runbook name matching multiple entries reports a fatal error."""
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(errors_conf_dir),
            "runbook",
            "resource_group",
            "duplicate",
        ],
    )
    assert result.exit_code == 1
    assert "ambiguous" in result.stderr


def test_runbook_missing_required_env_fails(
    runner: CliRunner, errors_conf_dir: Path
) -> None:
    """Test that a required, undefined runbook env var reports a fatal error."""
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(errors_conf_dir),
            "runbook",
            "resource_group",
            "needs-env",
        ],
    )
    assert result.exit_code == 1
    assert "REQUIRED_VAR" in result.stderr


def test_runbook_with_required_env_set_succeeds(
    runner: CliRunner, errors_conf_dir: Path
) -> None:
    """Test that supplying the required env var lets the runbook execute."""
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(errors_conf_dir),
            "runbook",
            "resource_group",
            "needs-env",
        ],
        env={"REQUIRED_VAR": "test_value"},
    )
    stdout, _ = assert_result(result)
    assert "ok" in stdout


def test_runbook_failure_propagates_exit_code(
    runner: CliRunner, errors_conf_dir: Path
) -> None:
    """Test that a runbook exiting with a non-zero code propagates that exit code."""
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(errors_conf_dir),
            "runbook",
            "resource_group",
            "fails",
        ],
    )
    assert result.exit_code == 3
