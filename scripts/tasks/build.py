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
import platform
import stat
from pathlib import Path
from typing import Final

from scripts.binds.pyinstaller import PyInstaller
from scripts.binds.uv import Uv
from terranova.process import Command

DIST_DIR: Final[Path] = (Path(__file__).parent.parent.parent / "dist").absolute()
SPEC_PATH: Final[Path] = Path("terranova.spec")
DISTRIBUTIONS_TARBALL_PATH: Final[Path] = Path("distributions") / "tarball"
COMPLETION_FILES: Final[dict[str, str]] = {
    "bash": "terranova.bash",
    "zsh": "_terranova",
    "fish": "terranova.fish",
}


def run() -> None:
    """Build a standalone terranova bundle for the current platform."""
    version = Uv().project_version()

    # Create dist dir
    DIST_DIR.mkdir(parents=True, exist_ok=False)

    system = platform.system().lower()
    spec_name = "macOS" if system == "darwin" else "linux"
    spec_src = DISTRIBUTIONS_TARBALL_PATH / f"terranova.{spec_name}.spec"

    try:
        SPEC_PATH.write_text(spec_src.read_text())
        PyInstaller().build(SPEC_PATH.as_posix())
    finally:
        SPEC_PATH.unlink(missing_ok=True)

    # Make terranova executable
    terranova_exec = DIST_DIR / "terranova" / "terranova"
    terranova_exec.chmod(terranova_exec.stat().st_mode | stat.S_IEXEC)

    # Check terranova bundle is working
    Command(terranova_exec).args("--version").inherit_out().exec()

    # Generate shell completion scripts from the freshly built bundle
    completions_dir = DIST_DIR / "terranova" / "completions"
    completions_dir.mkdir()
    for shell, filename in COMPLETION_FILES.items():
        Command(terranova_exec).args("completion", shell).stdout(
            completions_dir / filename
        ).exec()

    # Create a tarball for macOS
    if system == "darwin":
        arch = platform.machine()
        arch = "amd64" if arch == "x86_64" else arch
        bundle_dir = DIST_DIR / f"terranova-{version}-{system}-{arch}"
        (DIST_DIR / "terranova").replace(bundle_dir)
        Command("tar").args(
            "-C",
            bundle_dir.as_posix(),
            "-czf",
            (DIST_DIR / f"terranova-{version}-{system}-{arch}.tar.gz").as_posix(),
            "./",
        ).inherit_out().exec()
