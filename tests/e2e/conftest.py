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
import os
from collections.abc import Iterator

import pytest
from click.testing import CliRunner, Result


@pytest.fixture(autouse=True)
def _restore_cwd() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    cwd = os.getcwd()
    yield
    os.chdir(cwd)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def assert_result(result: Result) -> tuple[str, str | None]:
    stdout = result.stdout
    stderr = result.stderr if result.stderr_bytes else None

    if result.exit_code > 0:
        print(f"stdout: {stdout}")
        print(f"stderr: {stderr}")
        assert result.exit_code == 0
    for pattern in ["Failed", "Error"]:
        assert pattern not in [stdout, stderr]
    return stdout, stderr
