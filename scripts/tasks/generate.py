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
from typing import Final

from scripts.binds.pyinstaller import PyInstaller

SPEC_PATH: Final[Path] = Path("terranova.spec")
DISTRIBUTIONS_TARBALL_PATH: Final[Path] = Path("distributions") / "tarball"


def run() -> None:
    """Generate PyInstaller configuration with included data files."""
    add_data = (
        ("src/terranova/schemas/", "terranova/schemas/"),
        ("src/terranova/templates/", "terranova/templates/"),
    )
    for system in ("macOS", "linux"):
        PyInstaller().generate(add_data=add_data)
        SPEC_PATH.rename(DISTRIBUTIONS_TARBALL_PATH / f"terranova.{system}.spec")
