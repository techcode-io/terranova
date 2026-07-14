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
import re
import sys
from io import StringIO

from scripts.utils import fatal
from terranova.process import Bind, CommandNotFound


class Uv(Bind):
    """Represents a bind to uv command."""

    def __init__(self) -> None:
        """Init uv bind."""
        try:
            super().__init__("uv")
        except CommandNotFound as err:
            fatal("detect uv binary", err)

    def project_version(self, package: str = "terranova") -> str:
        """Return the installed version of a package."""
        capture = StringIO()
        (
            self._cmd.args("pip", "show", package)
            .stdout(capture)
            .stderr(sys.stderr)
            .exec()
        )
        match = re.search(r"Version: (.*)", capture.getvalue())
        if not match:
            fatal(f"detect {package} version")
        return match.group(1).replace(".dev0", "-dev").strip()

    def run_poe(self, task: str) -> None:
        """Run a poe task."""
        self._cmd.args("run", "poe", task).inherit().exec()
