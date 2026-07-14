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
import os
import re
import sys
from pathlib import Path

from scripts.binds.gh import Gh
from scripts.binds.git import Git
from scripts.binds.uv import Uv
from scripts.utils import Constants
from terranova.process import ErrorReturnCode


def __set_version(version: str) -> None:
    # Update app version
    try:
        data = Constants.TERRANOVA_INIT_PATH.read_text()
    except Exception as err:
        print(
            f"The `{Constants.TERRANOVA_INIT_PATH.as_posix()}` can't be read",
            file=sys.stderr,
        )
        raise err

    data = re.sub(
        r"__version__ = \"(.*)\"", f'__version__ = "{version}"', data, count=1
    )
    try:
        Constants.TERRANOVA_INIT_PATH.write_text(data)
    except Exception as err:
        print(
            f"The `{Constants.TERRANOVA_INIT_PATH.as_posix()}` file can't be written",
            file=sys.stderr,
        )
        raise err

    # Update project version
    try:
        data = Constants.PYPROJECT_PATH.read_text()
    except Exception as err:
        print(
            f"The `{Constants.PYPROJECT_PATH.as_posix()}` can't be read",
            file=sys.stderr,
        )
        raise err

    data = re.sub(r"version = \"(.+)\"", f'version = "{version}"', data, count=1)
    try:
        Constants.PYPROJECT_PATH.write_text(data)
    except Exception as err:
        print(
            f"The `{Constants.PYPROJECT_PATH.as_posix()}` file can't be written",
            file=sys.stderr,
        )
        raise err


def pre() -> None:
    # Ensure we have inputs
    release_version = os.getenv("RELEASE_VERSION")
    if not release_version:
        return print(
            "You must define `RELEASE_VERSION` environment variable.", file=sys.stderr
        )
    if not re.match(r"(\d){1,2}\.(\d){1,2}\.(\d){1,2}", release_version):
        return print(
            "The `RELEASE_VERSION` should match semver format.", file=sys.stderr
        )

    # Create a new branch
    # TODO: if you change the branch_name, please update the
    #       condition at .github/workflows/ci.yml
    branch_name = f"feat/pre-release-v{release_version}"
    git = Git()
    git.checkout_new_branch(branch_name)

    # Update all files
    __set_version(release_version)

    # Push release branch
    git.add_all()
    git.commit(f"release: terranova v{release_version}", no_verify=True)
    git.push("origin", branch_name)

    # Create a PR
    Gh().pr_create(base="main", head=branch_name)


def run() -> None:
    # Read project version
    release_version = Uv().project_version()

    # Create the release tag
    try:
        git = Git()
        git.tag(release_version)
        git.push("origin", release_version, inherit_out=False)
    except ErrorReturnCode:
        return print(
            f"The release `v{release_version}` already exists.", file=sys.stderr
        )

    # Create the release
    binaries = [file.absolute().as_posix() for file in Path(".").glob("./terranova-*")]
    Gh().release_create(release_version, f"terranova v{release_version}", binaries)


def post() -> None:
    # Ensure we have inputs
    next_version = os.getenv("NEXT_VERSION")
    if not next_version:
        return print(
            "You must define `NEXT_VERSION` environment variable", file=sys.stderr
        )
    if not re.match(r"(\d){1,2}\.(\d){1,2}\.(\d){1,2}-dev", next_version):
        return print("The `NEXT_VERSION` should match semver format.", file=sys.stderr)

    # Create a new branch
    # TODO: if you change the branch_name, please update the
    #       condition at .github/workflows/ci.yml
    branch_name = f"feat/post-release-v{next_version}"
    git = Git()
    git.checkout_new_branch(branch_name)

    # Update all files
    __set_version(next_version)

    # Push changes
    git.add_all()
    git.commit("chore: prepare for next iteration", no_verify=True)
    git.push("origin", branch_name)

    # Create a PR
    Gh().pr_create(base="main", head=branch_name)
