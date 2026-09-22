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
from pathlib import Path


class ExplainedError(Exception):
    """
    Represents an explained error.
    The error should contain the cause and a possible resolution.
    """

    def __init__(self, cause: str, resolution: str | None = None) -> None:
        """Init explained error."""
        super().__init__(cause)
        self.__cause = cause
        self.__resolution = resolution

    @property
    def cause(self) -> str:
        """
        Returns:
            cause of the error.
        """
        return self.__cause

    @property
    def resolution(self) -> str | None:
        """
        Returns:
            possible resolution of the error.
        """
        return self.__resolution


class ManifestError(ExplainedError):
    """Represents an invalid manifest."""


class InvalidManifestError(ManifestError):
    """Represents an invalid manifest error."""

    def __init__(self, path: Path) -> None:
        """Init invalid manifest error."""
        super().__init__(
            cause=f"Invalid `manifest.yml` file at `{path.as_posix()}`",
            resolution="Check the syntax or the version of the manifest.",
        )


class VersionManifestError(ManifestError):
    """Represents an version manifest error."""

    def __init__(self, version: str) -> None:
        """Init version manifest error."""
        super().__init__(
            cause=f"Manifest version `v{version}` isn't supported",
            resolution="Upgrade `terranova` version or downgrade manifest version.",
        )


class MissingManifestError(ManifestError):
    """Represents a missing manifest error."""

    def __init__(self, path: Path) -> None:
        """Init missing manifest error."""
        super().__init__(
            cause=f"Missing `manifest.yml` file at `{path.as_posix()}`",
            resolution="Create a manifest or use another location.",
        )


class UnreadableManifestError(ManifestError):
    """Represents an unreadable manifest error"""

    def __init__(self, path: Path) -> None:
        """Init missing manifest error."""
        super().__init__(
            cause=f"Unreadable `manifest.yml` file at `{path.as_posix()}`",
            resolution="Use the right user or change permissions.",
        )


class InvalidResourcesError(ExplainedError):
    """Represents an invalid resources configuration."""


class InteractiveApprovalError(ExplainedError):
    """Represents a request for interactive approval where it can't be honored."""

    def __init__(self) -> None:
        """Init interactive approval error."""
        super().__init__(
            cause=(
                "`--strategy parallel` runs multiple `terraform apply` "
                "processes at once, so none of them can prompt for "
                "interactive approval."
            ),
            resolution=(
                "Pass `--auto-approve`, or apply a saved plan "
                "(`terranova plan --out ...` then `terranova apply <file>.tnplan`)."
            ),
        )


class GitRepositoryError(ExplainedError):
    """Represents a failure to resolve the git repository backing the resources directory."""

    def __init__(self, resources_dir: Path) -> None:
        """Init git repository error."""
        super().__init__(
            cause=f"`{resources_dir.as_posix()}` isn't inside a git repository",
            resolution="Run `--auto-scope` from a git-managed directory, or omit the flag.",
        )


class GraphError(ExplainedError):
    """Represents an error building a dependency graph between resource groups."""


class CyclicImportError(GraphError):
    """Represents a cyclic dependency between resource groups' `imports`."""

    def __init__(self, rel_paths: list[str], edges: list[str] | None = None) -> None:
        """Init cyclic import error."""
        chain = " -> ".join(rel_paths)
        constraint = (
            "A resource group cannot depend, directly or indirectly, "
            "on a group that depends on it."
        )
        if edges:
            resolution = (
                "Remove or redirect one of these `imports` entries to break the cycle: "
                f"{'; '.join(edges)}. {constraint}"
            )
        else:
            resolution = (
                "Check the `imports` section of the involved manifests and remove the cycle. "
                f"{constraint}"
            )
        super().__init__(
            cause=f"A cyclic dependency was detected between resource groups: {chain}",
            resolution=resolution,
        )


class EngineError(ExplainedError):
    """Represents an error while resolving an engine binary."""


class UnsupportedEnginePlatformError(EngineError):
    """Represents an OS/architecture without official engine releases."""

    def __init__(self, system: str, machine: str) -> None:
        """Init unsupported engine platform error."""
        super().__init__(
            cause=f"No official engine release for `{system}/{machine}`",
            resolution="Set the engine version to `system` and install it manually.",
        )


class EngineDownloadError(EngineError):
    """Represents a failed engine download."""

    def __init__(self, engine_name: str, version: str, reason: str) -> None:
        """Init engine download error."""
        super().__init__(
            cause=f"Unable to download {engine_name} `{version}`: {reason}",
            resolution="Check the version exists and that the release server is reachable.",
        )


class EngineChecksumError(EngineError):
    """Represents an engine archive whose checksum doesn't match the published one."""

    def __init__(self, engine_name: str, version: str) -> None:
        """Init engine checksum error."""
        super().__init__(
            cause=f"Checksum mismatch for {engine_name} `{version}`",
            resolution="Retry the download; if it persists, do not use this binary.",
        )


class RunbookError(ExplainedError):
    """Represents a runbook error."""


class AmbiguousRunbookError(RunbookError):
    """Represents an ambiguous runbook error."""

    def __init__(self, name: str) -> None:
        """Init ambiguous runbook error."""
        super().__init__(
            cause=f"The runbook name `{name}` is ambiguous`",
            resolution="Ensure the runbook name is unique.",
        )


class MissingRunbookError(RunbookError):
    """Represents a missing runbook error."""

    def __init__(self, name: str) -> None:
        """Init missing runbook error."""
        super().__init__(
            cause=f"The runbook `{name}` isn't defined`",
            resolution="Ensure the runbook is defined.",
        )


class MissingRunbookEnvError(RunbookError):
    """Represents a missing runbook environment variables error."""

    def __init__(self, env_name: str) -> None:
        """Init missing runbook environment variables error."""
        super().__init__(
            cause=f"The environment variable `{env_name}` isn't defined.",
            resolution="Ensure the environment variable is defined before running the runbook.",
        )
