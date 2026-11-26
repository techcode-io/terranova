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
import sys
from io import StringIO

from proclet import Bind, CommandNotFound, ErrorReturnCode

from scripts.utils import fatal


class Git(Bind):
    """Represents a bind to git command."""

    def __init__(self) -> None:
        """Init git bind."""
        try:
            super().__init__("git")
        except CommandNotFound as err:
            fatal("detect git binary", err)

    def head(self) -> str:
        """Return the full commit hash of HEAD."""
        capture = StringIO()
        self._command("rev-parse", "HEAD").stdout(capture).stderr(sys.stderr).exec()
        return capture.getvalue().strip()

    def current_branch(self) -> str:
        """Return the name of the current branch."""
        capture = StringIO()
        self._command("rev-parse", "--abbrev-ref", "HEAD").stdout(capture).exec()
        return capture.getvalue().strip()

    def checkout(self, name: str) -> None:
        """Switch to an existing branch."""
        self._command("checkout", name).inherit_out().exec()

    def checkout_new_branch(self, name: str) -> None:
        """Create and switch to a new branch."""
        self._command("checkout", "-b", name).inherit_out().exec()

    def add_all(self) -> None:
        """Stage all changes."""
        self._command("add", "--all").inherit_out().exec()

    def commit(self, message: str, no_verify: bool = False) -> None:
        """Commit staged changes."""
        args = ["commit", "-m", message]
        if no_verify:
            args.append("--no-verify")
        self._command(*args).inherit_out().exec()

    def push(self, remote: str, ref: str) -> None:
        """Push a ref to a remote."""
        self._command("push", remote, ref).inherit_out().exec()

    def tag(self, name: str) -> None:
        """
        Create a tag.

        Raises:
            ErrorReturnCode: if the tag already exists.
        """
        self._command("tag", name).exec()

    def branch_delete(self, name: str) -> None:
        """Delete a branch if it exists."""
        try:
            self._command("branch", "-D", name).inherit_out().exec()
        except ErrorReturnCode:
            pass

    def status_short(self) -> str:
        """Return the short status of the working tree."""
        capture = StringIO()
        self._command("status", "-s").stdout(capture).stderr(sys.stderr).exec()
        return capture.getvalue().strip()
