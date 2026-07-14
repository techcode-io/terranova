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
from scripts.utils import fatal
from terranova.process import Bind, CommandNotFound


class Gh(Bind):
    """Represents a bind to gh command."""

    def __init__(self) -> None:
        """Init gh bind."""
        try:
            super().__init__("gh")
        except CommandNotFound as err:
            fatal("detect gh binary", err)

    def pr_create(self, base: str, head: str) -> None:
        """Create a pull request, filling title/body from commits."""
        (
            self._cmd.args("pr", "create", "--fill", f"--base={base}", f"--head={head}")
            .inherit_out()
            .exec()
        )

    def release_create(self, version: str, title: str, binaries: list[str]) -> None:
        """Create a release with generated notes and attached binaries."""
        args = [
            "release",
            "create",
            "--generate-notes",
            "--latest",
            f"--title={title}",
            version,
            *binaries,
        ]
        self._cmd.args(*args).inherit_out().exec()
