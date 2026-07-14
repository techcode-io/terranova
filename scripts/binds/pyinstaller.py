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

    def run(self, *args: str) -> None:
        """Run pyinstaller with the given arguments."""
        self._cmd.args(*args).inherit_out().exec()
