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
from proclet import Bind, Command, CommandNotFound, EnvCmd

from scripts.utils import fatal


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

        super().__init__(backend)
        env = EnvCmd.inherit()
        if backend == "podman":
            env.add({"BUILDAH_FORMAT": "docker"})
        self.env(env.build())
