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
import contextlib
import ctypes
import json
import os
import re
import shutil
import sys
from asyncio import (
    IncompleteReadError,
    LimitOverrunError,
    Server,
    StreamReader,
    StreamWriter,
    gather,
    open_connection,
    start_server,
)
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Final, NamedTuple, NotRequired, TypedDict

from scripts.binds.devcontainer import DevContainer
from scripts.utils import fatal

WORKSPACE_FOLDER: Final[str] = "."
# Overrides the container engine, which is otherwise the first of podman and docker on the PATH.
CONTAINER_ENGINE_ENV: Final[str] = "CONTAINER_ENGINE"
# Where the workspace is mounted in the container; read by `.devcontainer/devcontainer.json`.
CONTAINER_WORKSPACE_ENV: Final[str] = "CONTAINER_WORKSPACE"
GENERATED_DIR: Final[Path] = Path(WORKSPACE_FOLDER) / ".devcontainer" / ".generated"
# The host's Claude Code state for this project (sessions and memory) and the name of its
# directory in the container's `~/.claude/projects`; both read by `.devcontainer/devcontainer.json`.
CLAUDE_PROJECT_DIR_ENV: Final[str] = "CLAUDE_PROJECT_DIR"
CLAUDE_PROJECT_KEY_ENV: Final[str] = "CLAUDE_PROJECT_KEY"
CONTAINER_CLAUDE_PROJECTS: Final[str] = "/home/vscode/.claude/projects"

# The IDE plugin listens on an ephemeral loopback port of the host and writes that port plus a
# per-start auth token to `~/.claude/ide/<port>.lock`. The sandbox can reach neither. So the host
# runs a small relay on a free loopback port picked by the OS, which the container reaches through
# `host.containers.internal` (podman) or `host.docker.internal` (Docker), and which forwards to the IDE while adding the auth token itself -
# the token never enters the container.
# The port is handed over through this environment variable: `devcontainer.json` passes it to
# `.devcontainer/init-sandbox.sh` (which opens the firewall for it) and `exec` passes it to
# `.devcontainer/claude.sh`.
IDE_RELAY_PORT_ENV: Final[str] = "IDE_RELAY_PORT"
IDE_AUTH_HEADER: Final[bytes] = b"X-Claude-Code-Ide-Authorization"
# Win32 constants for `_process_alive`.
_PROCESS_QUERY_LIMITED_INFORMATION: Final[int] = 0x1000
_STILL_ACTIVE: Final[int] = 259


class IdeLockFile(TypedDict):
    """The JSON the IDE plugin writes to `~/.claude/ide/<port>.lock`."""

    pid: int
    authToken: str
    workspaceFolders: NotRequired[list[str]]
    ideName: NotRequired[str]


@dataclass(frozen=True)
class IdeLock:
    """A running IDE's Claude Code integration endpoint."""

    port: int
    auth_token: str
    name: str


class IdeRelay(NamedTuple):
    """A running relay: the listening server, its port and the IDE it forwards to."""

    server: Server
    port: int
    ide_lock: IdeLock


async def claude() -> None:
    """Launch Claude Code inside the `.devcontainer` sandbox, backed by podman or docker.

    Permission prompts are skipped: the container and its firewall are the boundary. If an IDE
    with the Claude Code plugin has this project open, its selection/diagnostics context is
    bridged in through a host-side relay (see `_start_ide_relay`).
    """
    workspace = Path(WORKSPACE_FOLDER).resolve()
    engine = _container_engine()
    container_workspace = _container_workspace(workspace)
    os.environ[CONTAINER_WORKSPACE_ENV] = (
        container_workspace  # read by devcontainer.json
    )
    claude_project_mount = _share_claude_project(workspace, container_workspace)
    devcontainer = DevContainer()

    # Start the IDE relay first: its port is only known once bound, and the container's firewall
    # is configured for it while the container starts.
    relay = await _start_ide_relay(workspace)
    if relay:
        os.environ[IDE_RELAY_PORT_ENV] = str(relay.port)
    else:
        os.environ.pop(IDE_RELAY_PORT_ENV, None)
    try:
        await _run_sandbox(
            devcontainer,
            workspace,
            engine,
            container_workspace,
            claude_project_mount,
            relay,
        )
    finally:
        if relay:
            relay.server.close()
            relay.server.close_clients()
            await relay.server.wait_closed()


async def _run_sandbox(
    devcontainer: DevContainer,
    workspace: Path,
    engine: str,
    container_workspace: str,
    claude_project_mount: str,
    relay: IdeRelay | None,
) -> None:
    """Start the sandbox container, run Claude Code in it, then stop the container."""
    stale = devcontainer.has_stale_container(
        workspace, [container_workspace, claude_project_mount], engine
    )
    if stale:
        print("Sandbox container is out of date with .devcontainer, recreating it.")
    try:
        devcontainer.up(WORKSPACE_FOLDER, engine, remove_existing_container=stale)
    finally:
        # Holds the host's OAuth token, already copied into the container's own volume by now.
        shutil.rmtree(GENERATED_DIR, ignore_errors=True)

    # Set terminal
    _reset_terminal()
    _set_terminal_title(f"Claude Sandbox · {workspace.name}")
    if relay:
        print(f"Bridging {relay.ide_lock.name} into the sandbox.")

    command = ["bash", f"{container_workspace}/.devcontainer/claude.sh"]
    env = _terminal_env()
    if relay:
        command.append(relay.ide_lock.name)
        env[IDE_RELAY_PORT_ENV] = str(relay.port)
    try:
        await devcontainer.exec(
            workspace, engine, container_workspace, *command, env=env
        )
    finally:
        print("Stopping the sandbox container.")
        devcontainer.stop(workspace, engine)


def _claude_config_dir() -> Path:
    """The host's Claude Code config directory."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _claude_project_key(path: str) -> str:
    """Name of a project's directory under `~/.claude/projects`: non-alphanumerics become `-`."""
    return re.sub(r"[^a-zA-Z0-9]", "-", path)


def _share_claude_project(workspace: Path, container_workspace: str) -> str:
    """Expose the host's sessions and memory for this project to the container.

    Claude Code keeps them in `~/.claude/projects/<key>/`, the key derived from the working
    directory, which is `container_workspace` inside the container. Creates the host directory
    (a bind mount needs its source to exist) and its `memory/` subdirectory, which
    `devcontainer.json` mounts read-only so a session running without permission prompts can't plant
    instructions that host sessions load automatically. Returns the container-side mount point.
    """
    project_dir = (
        _claude_config_dir() / "projects" / _claude_project_key(str(workspace))
    )
    (project_dir / "memory").mkdir(parents=True, exist_ok=True)
    key = _claude_project_key(container_workspace)
    os.environ[CLAUDE_PROJECT_DIR_ENV] = project_dir.as_posix()
    os.environ[CLAUDE_PROJECT_KEY_ENV] = key
    return f"{CONTAINER_CLAUDE_PROJECTS}/{key}"


def _container_engine() -> str:
    """Pick the container engine: `CONTAINER_ENGINE`, else podman, else docker."""
    if engine := os.environ.get(CONTAINER_ENGINE_ENV):
        return engine
    for engine in ("podman", "docker"):
        if shutil.which(engine):
            return engine
    fatal(f"detect podman or docker (or set {CONTAINER_ENGINE_ENV}) to run the sandbox")


def _container_workspace(workspace: Path) -> str:
    """Where the workspace is mounted in the container.

    macOS and Linux hosts mount it at its own absolute path, so the paths the IDE integration
    reports (host paths) resolve inside the container. A Windows path isn't a valid Linux path,
    so there it goes under /workspaces (the IDE's file paths then don't resolve in the container).
    """
    if os.name == "nt":
        return f"/workspaces/{workspace.name}"
    return workspace.as_posix()


def _reset_terminal() -> None:
    """Undo terminal modes the container build output may have left behind, then clear the screen.

    Build progress bars use cursor control sequences; a fullscreen UI started on top of a terminal
    left with the cursor hidden, line wrap off or a scroll region set renders broken.
    """
    if sys.stdout.isatty():
        # leave the alternate screen, show the cursor, re-enable line wrap, reset the scroll region
        # and text attributes, then clear the screen and home the cursor (scrollback is kept)
        sys.stdout.write("\033[?1049l\033[?25h\033[?7h\033[r\033[0m\033[2J\033[H")
        sys.stdout.flush()


def _terminal_env() -> dict[str, str]:
    """Host terminal capabilities to hand to the container, which has no way to know them.

    The size isn't among them: the engine's `exec -it` sizes the pty and keeps it in sync on resize.
    """
    if not sys.stdout.isatty():
        return {}
    return {
        name: value for name in ("TERM", "COLORTERM") if (value := os.environ.get(name))
    }


def _set_terminal_title(title: str) -> None:
    """Name the terminal tab (OSC 0, understood by PyCharm's terminal); no-op off a terminal."""
    if sys.stdout.isatty():
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()


def _process_alive(pid: int) -> bool:
    """Tell whether a process exists, without ever signalling it.

    `os.kill(pid, 0)` is the POSIX idiom, but on Windows every signal other than Ctrl-C/Ctrl-Break
    terminates the process - here, the user's IDE.
    """
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(  # pyright: ignore[reportAny]
            _PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(  # pyright: ignore[reportAny]
                handle, ctypes.byref(exit_code)
            )
            return bool(ok) and exit_code.value == _STILL_ACTIVE  # pyright: ignore[reportAny]
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def _find_ide_lock(workspace: Path) -> IdeLock | None:
    """Find the live IDE whose open workspace contains `workspace`, most recent first."""
    config_dir = _claude_config_dir()
    found: list[tuple[float, IdeLock]] = []
    for path in (config_dir / "ide").glob("*.lock"):
        try:
            # The annotation is static only: a malformed file still lands in the `except` below.
            data: IdeLockFile = json.loads(path.read_text())  # pyright: ignore[reportAny]
            if not _process_alive(data["pid"]):  # lock files outlive a crashed IDE
                continue
            lock = IdeLock(
                port=int(path.stem),
                auth_token=data["authToken"],
                name=data.get("ideName") or "IDE",
            )
            folders = [Path(f).resolve() for f in data.get("workspaceFolders", [])]
            mtime = path.stat().st_mtime
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if any(workspace == f or f in workspace.parents for f in folders):
            found.append((mtime, lock))
    return max(found, key=lambda entry: entry[0])[1] if found else None


async def _start_ide_relay(workspace: Path) -> IdeRelay | None:
    """Start the IDE relay if an IDE has this project open; warn and skip otherwise."""
    ide = _find_ide_lock(workspace)
    if ide is None:
        print("No IDE with Claude Code integration found, starting without it.")
        return None
    try:
        server = await start_server(partial(_relay_ide_connection, ide), "127.0.0.1", 0)
    except OSError as err:
        print(
            f"Can't listen on a loopback port for the IDE relay ({err}), "
            + "starting without IDE integration.",
            file=sys.stderr,
        )
        return None
    port: int = server.sockets[0].getsockname()[1]  # pyright: ignore[reportAny]
    return IdeRelay(server, port, ide)


async def _relay_ide_connection(
    ide: IdeLock, reader: StreamReader, writer: StreamWriter
) -> None:
    """Forward one connection to the IDE, replacing the auth header of its handshake."""
    upstream: StreamWriter | None = None
    try:
        head = await reader.readuntil(b"\r\n\r\n")
        # Whatever the client sent as auth (the container only holds a placeholder) is replaced
        # with the real token.
        lines = [
            line
            for line in head[:-4].split(b"\r\n")
            if not line.lower().startswith(IDE_AUTH_HEADER.lower() + b":")
        ]
        lines.append(IDE_AUTH_HEADER + b": " + ide.auth_token.encode())

        up_reader, upstream = await open_connection("127.0.0.1", ide.port)
        upstream.write(b"\r\n".join(lines) + b"\r\n\r\n")
        await gather(_pipe(reader, upstream), _pipe(up_reader, writer))
    except (OSError, IncompleteReadError, LimitOverrunError):
        pass
    finally:
        writer.close()
        if upstream:
            upstream.close()


async def _pipe(reader: StreamReader, writer: StreamWriter) -> None:
    """Copy bytes until either side closes."""
    with contextlib.suppress(OSError):
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    writer.close()
