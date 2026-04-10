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
from click.testing import CliRunner

from terranova.cli import main
from tests import PROJECT_TESTS_FIXTURES_DIR
from tests.e2e.conftest import assert_result


def test_runbook_with_env_if_is_defined_when_var_is_not_set(
    runner: CliRunner,
) -> None:
    """Test that runbook with if: is_defined condition succeeds when env var is not set."""
    fixture_conf_dir = PROJECT_TESTS_FIXTURES_DIR / "runbook_with_is_defined"
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(fixture_conf_dir),
            "runbook",
            "resource_group",
            "check-optional-env",
        ],
    )
    stdout, _ = assert_result(result)
    assert "OPTIONAL_VAR=" in stdout


def test_runbook_with_env_if_is_defined_when_var_is_set(runner: CliRunner) -> None:
    """Test that runbook with if: is_defined condition receives env var when set."""
    fixture_conf_dir = PROJECT_TESTS_FIXTURES_DIR / "runbook_with_is_defined"
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(fixture_conf_dir),
            "runbook",
            "resource_group",
            "check-optional-env",
        ],
        env={"OPTIONAL_VAR": "test_value"},
    )
    stdout, _ = assert_result(result)
    assert "OPTIONAL_VAR=test_value" in stdout
