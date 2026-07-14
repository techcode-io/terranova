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


class BasedPyright(Bind):
    """Represents a bind to basedpyright command."""

    def __init__(self) -> None:
        """Init basedpyright bind."""
        try:
            super().__init__("basedpyright")
        except CommandNotFound as err:
            fatal("detect basedpyright binary", err)

    def check(self) -> None:
        """
        Type check the project.

        Raises:
            ErrorReturnCode: if type errors are found.
        """
        self._cmd.inherit_out().exec()
