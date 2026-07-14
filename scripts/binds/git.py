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

from scripts.utils import fatal
from terranova.process import Bind, CommandNotFound, ErrorReturnCode


class Git(Bind):
    """Represents a bind to git command."""

    def __init__(self) -> None:
        """Init git bind."""
        try:
            super().__init__("git")
        except CommandNotFound as err:
            fatal("detect git binary", err)

    def short_head(self) -> str:
        """Return the short commit hash of HEAD."""
        capture = StringIO()
        self._cmd.args("rev-parse", "--short", "HEAD").stdout(capture).exec()
        return capture.getvalue().strip()

    def head(self) -> str:
        """Return the full commit hash of HEAD."""
        capture = StringIO()
        self._cmd.args("rev-parse", "HEAD").stdout(capture).stderr(sys.stderr).exec()
        return capture.getvalue().strip()

    def current_branch(self) -> str:
        """Return the name of the current branch."""
        capture = StringIO()
        self._cmd.args("rev-parse", "--abbrev-ref", "HEAD").stdout(capture).exec()
        return capture.getvalue().strip()

    def checkout(self, name: str, inherit_out: bool = False) -> None:
        """Switch to an existing branch."""
        cmd = self._cmd.args("checkout", name)
        if inherit_out:
            cmd.inherit_out()
        cmd.exec()

    def checkout_new_branch(self, name: str) -> None:
        """Create and switch to a new branch."""
        self._cmd.args("checkout", "-b", name).inherit_out().exec()

    def add_all(self) -> None:
        """Stage all changes."""
        self._cmd.args("add", "--all").inherit_out().exec()

    def commit(self, message: str, no_verify: bool = False) -> None:
        """Commit staged changes."""
        args = ["commit", "-m", message]
        if no_verify:
            args.append("--no-verify")
        self._cmd.args(*args).inherit_out().exec()

    def push(self, remote: str, ref: str, inherit_out: bool = True) -> None:
        """Push a ref to a remote."""
        cmd = self._cmd.args("push", remote, ref)
        if inherit_out:
            cmd.inherit_out()
        cmd.exec()

    def tag(self, name: str) -> None:
        """
        Create a tag.

        Raises:
            ErrorReturnCode: if the tag already exists.
        """
        self._cmd.args("tag", name).exec()

    def branch_delete(self, name: str) -> None:
        """Delete a branch if it exists."""
        try:
            self._cmd.args("branch", "-D", name).inherit_out().exec()
        except ErrorReturnCode:
            pass

    def status_short(self) -> str:
        """Return the short status of the working tree."""
        capture = StringIO()
        self._cmd.args("status", "-s").stdout(capture).stderr(sys.stderr).exec()
        return capture.getvalue().strip()
