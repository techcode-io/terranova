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
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest
from click.testing import CliRunner, Result

from terranova.binds import Git
from terranova.engines import EngineManager
from terranova.exceptions import EngineError
from terranova.resources import ResourcesEngine

# Needs `terraform_data` (1.4+); pinned so the checksum-verified download is reproducible.
REAL_TERRAFORM_VERSION: Final[str] = "1.9.8"


@pytest.fixture(autouse=True)
def _restore_cwd() -> Iterator[None]:
    cwd = os.getcwd()
    yield
    os.chdir(cwd)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point `EngineManager`'s cache at `tmp_path` through the home directory."""
    monkeypatch.setenv("HOME", tmp_path.as_posix())
    monkeypatch.setenv("USERPROFILE", tmp_path.as_posix())
    return tmp_path / ".terranova" / "engines"


def copy_as_git_repo(fixture_dir: Path, dest: Path) -> None:
    """
    Copy `fixture_dir` to `dest` and commit it as a fresh git repo.

    For `--auto-scope` e2e coverage: fixtures under `tests/fixtures` are
    shared, read-only trees, so scoping tests that need to dirty the working
    tree operate on a throwaway git-managed copy instead.
    """
    shutil.copytree(fixture_dir, dest)

    git = Git(dest)
    git.init()
    git.add()
    git.commit("initial", author=("test", "test@example.com"))


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


@pytest.fixture(scope="session")
def real_terraform_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Install a real terraform with the `EngineManager`, once per test session.

    The cache lives next to pytest's base temp dir, which `xdist` workers share, so the
    manager's atomic install keeps concurrent workers safe and later runs reuse the download.
    Without network access the dependent tests are skipped locally, but fail on CI.
    """
    home = tmp_path_factory.getbasetemp().parent / "engines-home"
    with pytest.MonkeyPatch.context() as patch:
        # `EngineManager` caches under the home directory
        patch.setenv("HOME", home.as_posix())
        patch.setenv("USERPROFILE", home.as_posix())
        try:
            binary = EngineManager().resolve(
                ResourcesEngine("terraform", REAL_TERRAFORM_VERSION)
            )
        except EngineError as err:
            if os.environ.get("CI"):
                raise
            pytest.skip(f"cannot install terraform {REAL_TERRAFORM_VERSION}: {err}")
    assert binary is not None
    return binary


@pytest.fixture
def real_terraform(
    real_terraform_binary: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Put a real terraform first on `PATH`, instead of the fake one of `fake_terraform_bin`."""
    path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{real_terraform_binary.parent}{os.pathsep}{path}")
    return real_terraform_binary
