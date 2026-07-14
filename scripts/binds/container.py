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
from io import StringIO
from pathlib import Path
from typing import override

from scripts.utils import fatal
from terranova.process import Bind, Command, CommandNotFound, EnvCmd


class Container(Bind):
    """Represents a bind to a container backend, either docker or podman."""

    def __init__(self) -> None:
        """Init container bind."""
        backend: str | None = None
        for candidate in ("docker", "podman"):
            try:
                Command(candidate)
                backend = candidate
                break
            except CommandNotFound:
                continue

        if backend is None:
            fatal(
                "detect container backend",
                CommandNotFound("docker or podman"),
            )

        self.__backend: str = backend
        super().__init__(backend)

    @override
    def create(self, cmd_path: str | Path) -> Command:
        env = EnvCmd.inherit()
        if self.__backend == "podman":
            env.add({"BUILDAH_FORMAT": "docker"})
        return Command(cmd_path).env(env.build())

    def build_image(self, platform: str, python_version: str, tag: str) -> None:
        """Build a multi-platform image via buildx."""
        (
            self._cmd.args(
                "buildx",
                "build",
                "--load",
                "--platform",
                platform,
                "--build-arg",
                f"base_image_version={python_version}",
                "-t",
                tag,
                "-f",
                "Containerfile",
                ".",
            )
            .inherit_out()
            .exec()
        )

    def run_detached(self, platform: str, image: str) -> str:
        """Run a detached container with a no-op entrypoint and return its id."""
        capture = StringIO()
        (
            self._cmd.args(
                "run",
                "-d",
                "--platform",
                platform,
                "--entrypoint=cat",
                image,
            )
            .stdout(capture)
            .exec()
        )
        return capture.getvalue().strip()

    def copy_from(self, container_id: str, container_path: str, dest: Path) -> None:
        """Copy a path out of a container."""
        (
            self._cmd.args(
                "cp",
                f"{container_id}:{container_path}",
                dest.as_posix(),
            )
            .inherit_out()
            .exec()
        )

    def remove(self, container_id: str) -> None:
        """Remove a container."""
        self._cmd.args("rm", "-f", container_id).exec()
