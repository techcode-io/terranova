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
import sys
from pathlib import Path
from time import time

from scripts.binds.container import Container
from scripts.binds.git import Git
from scripts.binds.pyinstaller import PyInstaller
from scripts.binds.uv import Uv
from scripts.utils import Constants


def run() -> None:
    commit_hash_short = Git().short_head()
    current_time_epoch = int(time())
    version = Uv().project_version()
    python_version = platform.python_version()

    image_id = f"{version}-{current_time_epoch}-{commit_hash_short}"

    # Create dist dir
    local_dist_path = Path("dist")
    local_dist_path.mkdir(parents=True, exist_ok=False)
    local_dist_path = local_dist_path.absolute()

    system = platform.system().lower()
    match system:
        case "darwin":
            PyInstaller().build("terranova.spec")
            arch = platform.machine()
            arch = "amd64" if arch == "x86_64" else arch
            Path("./dist/terranova").replace(
                Path(f"./dist/terranova-{version}-{system}-{arch}")
            )
        case "linux":
            # Use cross-build to build both amd64 and arm64 versions.
            container = Container()
            for arch in ["amd64", "arm64"]:
                platform_arch = f"linux/{arch}"
                tag = f"{Constants.REGISTRY_URL}/terranova:{image_id}"
                container.build_image(platform_arch, python_version, tag)
                container_id = container.run_detached(platform_arch, tag)
                container.copy_from(
                    container_id,
                    "/opt/terranova/dist/terranova",
                    local_dist_path / f"terranova-{version}-linux-{arch}",
                )
                container.remove(container_id)
        case _:
            print(f"Unsupported system: {system}", file=sys.stderr)
