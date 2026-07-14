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

from scripts.binds.basedpyright import BasedPyright
from scripts.binds.git import Git
from scripts.binds.ruff import Ruff
from scripts.binds.uv import Uv
from scripts.utils import fatal
from terranova.process import ErrorReturnCode


def check_ruff() -> None:
    """Check codebase formatting and style with ruff."""
    print("Checking codebase")
    try:
        Ruff().check("src/terranova")
    except ErrorReturnCode as err:
        # Forward exit code without traceback
        sys.exit(err.exit_code)


def check_basedpyright() -> None:
    """Type check codebase with basedpyright."""
    print("Type checking codebase")
    try:
        BasedPyright().check()
    except ErrorReturnCode as err:
        # Forward exit code without traceback
        sys.exit(err.exit_code)


def check_license_headers() -> None:
    """Verify and apply Apache license headers to all project files."""
    print("Checking license headers")
    git = Git()

    head_commit_hash = git.head()
    current_branch_name = git.current_branch()

    branch_name = f"automation/lint-{head_commit_hash}"
    try:
        # Prepare the branch
        git.branch_delete(branch_name)
        git.checkout_new_branch(branch_name)

        # Apply headers licence
        Uv().run_poe("project:license")

        # Validate
        changes = git.status_short()
        if changes:
            fatal(f"Apply headers license to:\n{changes}")
    finally:
        git.checkout(current_branch_name)
        git.branch_delete(branch_name)


def run() -> None:
    """Run all lint checks: formatting, type checking, and license headers."""
    check_ruff()
    check_basedpyright()
    check_license_headers()
