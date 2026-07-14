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


class PyInstaller(Bind):
    """Represents a bind to pyinstaller command."""

    def __init__(self) -> None:
        """Init pyinstaller bind."""
        try:
            super().__init__("pyinstaller")
        except CommandNotFound as err:
            fatal("detect pyinstaller binary", err)

    def build(self, spec: str) -> None:
        """Build binary from a spec file."""
        self._cmd.args(spec).inherit_out().exec()

    def generate(
        self,
        exclude_modules: tuple[str, ...] = (),
        hidden_imports: tuple[str, ...] = (),
        add_data: tuple[tuple[str, str], ...] = (),
    ) -> None:
        """Generate pyinstaller config and build binary."""
        args = ["-n", "terranova", "--onefile", "--noconfirm", "--optimize=1"]
        for exclude_module in exclude_modules:
            args.extend(["--exclude-module", exclude_module])

        for hidden_import in hidden_imports:
            args.extend(["--hidden-import", hidden_import])

        for src, dst in add_data:
            args.extend(["--add-data", f"{src}:{dst}"])

        args.append("./bin/terranova")
        self._cmd.args(*args).inherit_out().exec()
