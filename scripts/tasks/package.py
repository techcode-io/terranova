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

from scripts.binds.nfpm import Nfpm

DISTRIBUTIONS_PACKAGES_PATH = "distributions/packages"


def __build_package(packager: str) -> None:
    """Build and package the current architecture bundle with nfpm."""
    arch = platform.machine().lower()
    arch = "amd64" if arch == "x86_64" else arch
    arch = "arm64" if arch == "aarch64" else arch

    Nfpm().package(
        config=f"{DISTRIBUTIONS_PACKAGES_PATH}/nfpm.{arch}.yaml",
        target="/tmp/dist",
        packager=packager,
    )


def deb() -> None:
    """Build a debian package."""
    __build_package("deb")


def rpm() -> None:
    """Build an rpm package."""
    __build_package("rpm")
