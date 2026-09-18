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
import json
import os
import re
import signal
import sys
from datetime import datetime
from io import StringIO
from pathlib import Path

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


class _ResizeForwarder:
    """Process observer relaying terminal resizes to a process running attached to the terminal.

    A terminal only signals its foreground process group (SIGWINCH), and `poe` runs its task in a
    group of its own, so the engine client never hears about resizes on its own. This polls the
    size instead (reading it needs no foreground rights) and signals the client, which then
    resizes the container's pty itself.
    """

    _INTERVAL = 0.2

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    def on_spawn(self, process: Process) -> None:
        if hasattr(signal, "SIGWINCH") and sys.stdout.isatty():
            self._task = asyncio.create_task(self._watch(process))

    def on_exit(self, process: Process) -> None:
        if self._task:
            self._task.cancel()

    async def _watch(self, process: Process) -> None:
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
                    process.send_signal(getattr(signal, "SIGWINCH"))


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

    def has_stale_container(
        self, workspace: Path, container_workspace: str, docker_path: str
    ) -> bool:
        """Tell whether the workspace's container no longer matches the `.devcontainer` config.

        `devcontainer up` reuses an existing container as-is, so it goes stale when either the
        workspace isn't mounted at `container_workspace` (container created before that layout) or one of
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
            edited = max(
                (
                    (workspace / ".devcontainer" / name).stat().st_mtime
                    for name in CONTAINER_INPUTS
                    if (workspace / ".devcontainer" / name).exists()
                ),
                default=0.0,
            )
            for container_id in ids:
                info = json.loads(self._capture(docker_path, "inspect", container_id))[
                    0
                ]
                if container_workspace not in [
                    m["Destination"] for m in info["Mounts"]
                ]:
                    return True
                # RFC 3339 with nanoseconds and possibly a trailing Z; trim to what fromisoformat takes.
                created = re.sub(r"(\.\d{6})\d*", r"\1", info["Created"]).replace(
                    "Z", "+00:00"
                )
                if edited > datetime.fromisoformat(created).timestamp():
                    return True
        except (
            OSError,
            CommandNotFound,
            ErrorReturnCode,
            ValueError,
            KeyError,
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
                # Login shell so PATH gets what the devcontainer features (node, claude) add.
                "bash",
                "-lc",
                'exec "$@"',
                "bash",
                *command,
            )
            .inherit()
            .add_observer(_ResizeForwarder())
            .aexec()
        )
