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
import asyncio
import contextlib
import os
import signal
import sys
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Final

from serde import SerdeError, serde
from serde.json import from_json

from scripts.utils import fatal
from terranova.process import (
    Bind,
    Command,
    CommandNotFound,
    ErrorReturnCode,
    Process,
)

# Files under `.devcontainer/` whose content ends up in the container itself (image, lifecycle
# commands, baked-in scripts), so editing one means the container has to be recreated.
CONTAINER_INPUTS = (
    "Containerfile",
    "devcontainer.json",
    "devcontainer-lock.json",
    "init-sandbox.sh",
    "post_create.py",
)


@serde(rename_all="pascalcase")
@dataclass(frozen=True)
class _Mount:
    destination: str


@serde(rename_all="pascalcase")
@dataclass(frozen=True)
class _ContainerInfo:
    """The subset of `docker inspect` output read here."""

    mounts: list[_Mount]
    created: datetime


class _ResizeForwarder:
    """Process observer relaying terminal resizes to a process running attached to the terminal.

    A terminal only signals its foreground process group (SIGWINCH), and `poe` runs its task in a
    group of its own, so the engine client never hears about resizes on its own. This polls the
    size instead (reading it needs no foreground rights) and signals the client, which then
    resizes the container's pty itself.
    """

    _INTERVAL: Final[float] = 0.2

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    def on_spawn(self, process: Process) -> None:
        """Start watching the terminal size, only on a POSIX interactive terminal."""
        if sys.platform != "win32" and sys.stdout.isatty():
            self._task = asyncio.create_task(self._watch(process))

    def on_exit(self, process: Process) -> None:
        """Stop watching; `process` is unused but required by `ProcessObserver`."""
        del process
        if self._task:
            self._task.cancel()

    async def _watch(self, process: Process) -> None:
        """Poll the terminal size and send SIGWINCH to `process` whenever it changes.

        Ends when the process exits or the terminal is no longer readable. Each tick is
        a couple of cheap syscalls, so it never blocks the event loop.
        """
        size = os.get_terminal_size(sys.stdout.fileno())
        while process.returncode is None:
            await asyncio.sleep(self._INTERVAL)
            try:
                current = os.get_terminal_size(sys.stdout.fileno())
            except OSError:
                return
            if current != size:
                size = current
                with contextlib.suppress(ProcessLookupError):
                    process.send_signal(signal.SIGWINCH)


class DevContainer(Bind):
    """Represents a bind to the `@devcontainers/cli` tool, run through `npx`.

    Used to build/start/exec into the sandbox defined in `.devcontainer/devcontainer.json`,
    keeping that file as the single source of truth for features, mounts and the firewall.
    """

    def __init__(self) -> None:
        """Init devcontainer bind."""
        try:
            super().__init__("npx")
        except CommandNotFound as err:
            fatal("detect npx binary (required to run @devcontainers/cli)", err)

    @staticmethod
    def _capture(binary: str, *args: str) -> str:
        """Run a command to completion and return its stdout."""
        out = StringIO()
        Command(binary).args(*args).stdout(out).exec()
        return out.getvalue()

    @staticmethod
    def _inputs_last_modified(workspace: Path) -> float:
        """Return the latest mtime (epoch seconds) among existing `CONTAINER_INPUTS`, or 0.0."""
        config_dir = workspace / ".devcontainer"
        paths = [config_dir / name for name in CONTAINER_INPUTS]
        return max(
            (path.stat().st_mtime for path in paths if path.exists()), default=0.0
        )

    def has_stale_container(
        self, workspace: Path, required_mounts: list[str], docker_path: str
    ) -> bool:
        """Tell whether the workspace's container no longer matches the `.devcontainer` config.

        `devcontainer up` reuses an existing container as-is, so it goes stale when either the
        container lacks one of `required_mounts` (created before that layout) or one of
        `CONTAINER_INPUTS` was edited after the container was created. Any problem querying the
        container engine counts as "not stale": `up` will then surface the real error.
        """
        try:
            ids = self._capture(
                docker_path,
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"label=devcontainer.local_folder={workspace}",
            ).split()
            edited = self._inputs_last_modified(workspace)
            for container_id in ids:
                inspected = from_json(
                    list[_ContainerInfo],
                    self._capture(docker_path, "inspect", container_id),
                )
                info = inspected[0]
                destinations = {m.destination for m in info.mounts}
                if not destinations.issuperset(required_mounts):
                    return True
                if edited > info.created.timestamp():
                    return True
        except (
            OSError,
            CommandNotFound,
            ErrorReturnCode,
            ValueError,
            SerdeError,
            IndexError,
        ):
            return False
        return False

    def up(
        self,
        workspace_folder: str,
        docker_path: str,
        remove_existing_container: bool = False,
    ) -> None:
        """Build the sandbox image if needed and start the devcontainer.

        Args:
            workspace_folder: project root, as understood by `@devcontainers/cli`.
            docker_path: container engine binary.
            remove_existing_container: recreate the container instead of reusing it.
        """
        (
            self._cmd.args(
                "--yes",
                "@devcontainers/cli",
                "up",
                "--workspace-folder",
                workspace_folder,
                "--docker-path",
                docker_path,
                *(["--remove-existing-container"] if remove_existing_container else []),
            )
            .inherit_out()
            .exec()
        )

    async def exec(
        self,
        workspace: Path,
        docker_path: str,
        container_workspace: str,
        *command: str,
        env: dict[str, str] | None = None,
    ) -> None:
        """Run a command inside the running devcontainer, attached to the current terminal.

        Goes through the container engine's own `exec -it` rather than `devcontainer exec`: the
        latter relays the terminal through its own pty and doesn't propagate window resizes, which
        corrupts a fullscreen UI. The engine's client resizes the pty when signalled (SIGWINCH).

        Async so the caller can keep other tasks (the IDE relay) running on the same event loop
        while the command is in the foreground.

        Args:
            workspace: project root on the host, used to find its container.
            docker_path: container engine binary.
            container_workspace: working directory in the container.
            command: command and arguments to run.
            env: extra environment variables for the command.
        """
        ids = self._capture(
            docker_path,
            "ps",
            "--quiet",
            "--filter",
            f"label=devcontainer.local_folder={workspace}",
        ).split()
        if not ids:
            fatal("find the running sandbox container")
        # Mirrors what the CLI applies from devcontainer.json (remoteUser, remoteEnv).
        env_args = [
            arg
            for name, value in {
                "DISABLE_ERROR_REPORTING": "1",
                "UV_CACHE_DIR": "/home/vscode/.cache/uv",
                "UV_PROJECT_ENVIRONMENT": "/home/vscode/.venv",
                "HISTFILE": "/commandhistory/.bash_history",
                **(env or {}),
            }.items()
            for arg in ("--env", f"{name}={value}")
        ]
        await (
            Command(docker_path)
            .args(
                "exec",
                "--interactive",
                "--tty",
                "--user",
                "vscode",
                "--workdir",
                container_workspace,
                *env_args,
                ids[0],
                *command,
            )
            .inherit()
            .add_observer(_ResizeForwarder())
            .aexec()
        )

    def stop(self, workspace: Path, docker_path: str) -> None:
        """Stop the workspace's running devcontainer, keeping it (and its volumes) for next time.

        Best-effort: this runs on the way out of a session, so a failure is reported, not raised.
        """
        try:
            ids = self._capture(
                docker_path,
                "ps",
                "--quiet",
                "--filter",
                f"label=devcontainer.local_folder={workspace}",
            ).split()
            if ids:
                Command(docker_path).args("stop", *ids).exec()
        except (OSError, CommandNotFound, ErrorReturnCode) as err:
            print(f"Failed to stop the sandbox container: {err}", file=sys.stderr)
