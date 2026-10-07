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
End-to-end run against a real terraform binary.

Everything else in `tests/e2e` uses a fake terraform. This module drives the complete
init -> plan -> apply -> output -> destroy flow through a real one, installed by the
`real_terraform` fixture, which is what proves the Windows support (symlinks, paths,
process handling). It only needs terraform's built-in `terraform_data`, so no provider
is downloaded.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests import PROJECT_TESTS_FIXTURES_DIR


def _terranova(
    conf_dir: Path, *args: str, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from terranova.cli import main; main()",
            "--conf-dir",
            str(conf_dir),
            *args,
        ],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )
    print(result.stdout, result.stderr)  # shown by pytest when an assertion fails
    return result


@pytest.fixture
def conf_dir(tmp_path: Path) -> Path:
    # `init` writes links and a `.terraform` dir next to the sources: work on a copy
    target = tmp_path / "conf"
    shutil.copytree(PROJECT_TESTS_FIXTURES_DIR / "real_terraform", target)
    return target


@pytest.mark.usefixtures("real_terraform")
def test_full_lifecycle_with_real_terraform(conf_dir: Path) -> None:
    group = conf_dir / "resources" / "group"
    plan_file = conf_dir / "plan.tnplan"

    assert _terranova(conf_dir, "init", "group").returncode == 0
    # Dependencies are symbolic links: a file and a directory
    assert (group / "00-versions.tf").is_symlink()
    assert (group / "assets").is_symlink()
    assert (group / "assets" / "greeting.tftpl").is_file()

    assert _terranova(conf_dir, "validate", "group").returncode == 0

    planned = _terranova(
        conf_dir, "plan", "group", "--no-input", "--out", str(plan_file)
    )
    assert planned.returncode == 0
    assert plan_file.is_file()

    applied = _terranova(conf_dir, "apply", str(plan_file), "--auto-approve")
    assert applied.returncode == 0

    shown = _terranova(conf_dir, "output", "group", "greeting")
    assert shown.returncode == 0
    assert "Hello, world!" in shown.stdout

    destroyed = _terranova(conf_dir, "destroy", "group", stdin="yes\n")
    assert destroyed.returncode == 0
