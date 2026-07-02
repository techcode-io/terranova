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
from __future__ import annotations

import asyncio
import os
import queue
import shutil
import subprocess
import sys
import threading
from asyncio import Task, create_task, gather, wait_for
from contextlib import suppress
from io import TextIOBase
from pathlib import Path
from threading import Thread
from typing import IO, Callable, Self, TextIO, overload, Protocol

from terranova.io import close

# Type aliases for process types
type SyncProcess = subprocess.Popen[bytes]
type AsyncProcess = asyncio.subprocess.Process
type Process = SyncProcess | AsyncProcess


class ProcessObserver(Protocol):
    """Observer notified at process spawn and exit."""

    def on_spawn(self, process: Process) -> None: ...

    def on_exit(self, process: Process) -> None: ...


class CommandNotFound(Exception):
    """Raised when a command binary is not found."""

    def __init__(self, command: str) -> None:
        super().__init__(f"Command not found: {command}")
        self.command: str = command


class ErrorReturnCode(Exception):
    """Raised when a command returns a non-zero exit code."""

    def __init__(self, cmd: str, exit_code: int) -> None:
        super().__init__(f"Command '{cmd}' returned non-zero exit code {exit_code}")
        self.__cmd = cmd
        self.__exit_code = exit_code

    @property
    def cmd(self) -> str:
        return self.__cmd

    @property
    def exit_code(self) -> int:
        return self.__exit_code


class TimeoutException(Exception):
    """Raised when a command times out."""

    def __init__(self, cmd: str, timeout: float) -> None:
        super().__init__(f"Command '{cmd}' timed out after {timeout} seconds")
        self.__cmd = cmd
        self.__timeout = timeout

    @property
    def cmd(self) -> str:
        return self.__cmd

    @property
    def timeout(self) -> float:
        return self.__timeout


# Redirect command types
type SyncRedirectIn = queue.Queue[bytes | str | None] | str | TextIO
type AsyncRedirectIn = asyncio.Queue[bytes | str | None] | str | TextIO
type RedirectIn = SyncRedirectIn | AsyncRedirectIn
type RedirectOut = Path | TextIO | Callable[[str], None]


def strip_redirect(log_fn: Callable[[str], None]) -> Callable[[str], None]:
    """Wrap a log callable to strip trailing newlines from process output lines.

    Use this when redirecting process stdout/stderr to a structlog logger so
    each line is emitted as a clean event without a trailing newline character.
    """
    return lambda line: log_fn(line.rstrip("\n"))


class EnvCmd:
    """Convenient environment variables builder for command."""

    def __init__(self) -> None:
        """Init env command."""
        self.__build: dict[str, str] = {}

    @staticmethod
    def empty() -> "EnvCmd":
        """Create an empty env command."""
        return EnvCmd()

    @staticmethod
    def inherit(predicate: Callable[[str, str], bool] | None = None) -> "EnvCmd":
        """Create an env command that inherit environment variables from the current process."""
        env = EnvCmd()
        if predicate:
            env.__build = {
                key: value for key, value in os.environ.items() if predicate(key, value)
            }
        else:
            env.__build = os.environ.copy()
        return env

    def add(self, env_vars: dict[str, str]) -> Self:
        """Add environment variables to the env command."""
        self.__build.update(env_vars)
        return self

    def build(self) -> dict[str, str]:
        """
        Returns:
            environment variables built using the env command.
        """
        return self.__build.copy()


class PathCmd:
    """Convenient path builder for command."""

    def __init__(self) -> None:
        """Init path command."""
        self.__build: list[str] = []

    @staticmethod
    def empty() -> "PathCmd":
        """Create an empty path command."""
        return PathCmd()

    @staticmethod
    def inherit() -> "PathCmd":
        """Create a path command that inherit the PATH environment variable from the current process."""
        path = PathCmd()
        path.__build = os.environ.get("PATH", "").split(os.pathsep)
        return path

    def add(self, value: str) -> Self:
        """Add a binary directory path to the program PATH."""
        self.__build.insert(0, value)
        return self

    def build(self) -> str:
        """
        Returns:
            computed PATH environment variable.
        """
        return os.pathsep.join(self.__build)


def _needs_forwarding(redirect: RedirectOut | None) -> bool:
    """Check if a redirect needs a forwarding thread."""
    if redirect is None:
        return False
    if callable(redirect):
        return True
    if isinstance(redirect, TextIOBase):
        # TextIOBase instances without a real fd (e.g. StringIO) need forwarding
        try:
            redirect.fileno()
            return False
        except (OSError, AttributeError):
            return True
    return False


class Command:
    """Convenient wrapper around subprocess for process execution."""

    def __init__(self, cmd_path: str | Path) -> None:
        """
        Init command.

        Args:
            cmd_path: command name or path.
        """
        if isinstance(cmd_path, Path):
            self.__cmd_path = cmd_path.as_posix()
        else:
            found_path = shutil.which(cmd_path)
            if found_path is None:
                raise CommandNotFound(cmd_path)
            self.__cmd_path = found_path
        self.__cwd = Path.cwd()
        self.__env: dict[str, str] = {}
        self.__args: tuple[str, ...] = ()
        self.__in: RedirectIn | None = None
        self.__out: RedirectOut | None = None
        self.__err: RedirectOut | None = None
        self.__timeout: int | None = None
        self.__observers: list[ProcessObserver] = []

    @overload
    def args(self) -> tuple[str, ...]: ...

    @overload
    def args(self, first: str, /, *value: str) -> Self: ...

    def args(self, *values: str) -> tuple[str, ...] | Self:
        """
        Returns current arguments if no value is provided, otherwise set arguments.

        Args:
            *values: arguments to set.
        Returns:
            current arguments if no value is provided or self for chaining.
        """
        if not values:
            return self.__args

        self.__args = values
        return self

    @overload
    def env(self) -> dict[str, str]: ...

    @overload
    def env(self, value: dict[str, str]) -> Self: ...

    def env(self, value: dict[str, str] | None = None) -> dict[str, str] | Self:
        """
        Returns current env vars if no value is provided, otherwise set env vars.

        Args:
            value: env vars to set.
        Returns:
            current env vars if no value is provided or self for chaining.
        """
        if value is None:
            return self.__env.copy()

        self.__env = value
        return self

    @overload
    def cwd(self) -> Path: ...

    @overload
    def cwd(self, value: Path) -> Self: ...

    def cwd(self, value: Path | None = None) -> Path | Self:
        """
        Returns current cwd if no value is provided, otherwise set cwd.

        Args:
            value: cwd to set.
        Returns:
            current cwd if no value is provided or self for chaining.
        """
        if value is None:
            return self.__cwd

        self.__cwd = value
        return self

    @overload
    def stdin(self) -> RedirectIn | None: ...

    @overload
    def stdin(self, value: RedirectIn) -> Self: ...

    def stdin(self, value: RedirectIn | None = None) -> RedirectIn | None | Self:
        """
        Returns current stdin if no value is provided, otherwise set stdin.

        Args:
            value: stdin to set.
        Returns:
            current stdin if no value is provided or self for chaining.
        """
        if value is None:
            return self.__in

        self.__in = value
        return self

    @overload
    def stdout(self) -> RedirectOut | None: ...

    @overload
    def stdout(self, value: RedirectOut) -> Self: ...

    def stdout(self, value: RedirectOut | None = None) -> RedirectOut | None | Self:
        """
        Returns current stdout if no value is provided, otherwise set stdout.

        Args:
            value: stdout to set.
        Returns:
            current stdout if no value is provided or self for chaining.
        """
        if value is None:
            return self.__out
        elif isinstance(value, Path):
            self.__out = value.absolute()
        else:
            self.__out = value
        return self

    @overload
    def stderr(self) -> RedirectOut | None: ...

    @overload
    def stderr(self, value: RedirectOut) -> Self: ...

    def stderr(self, value: RedirectOut | None = None) -> RedirectOut | None | Self:
        """
        Returns current stderr if no value is provided, otherwise set stderr.

        Args:
            value: stderr to set.
        Returns:
            current stderr if no value is provided or self for chaining.
        """
        if value is None:
            return self.__err
        elif isinstance(value, Path):
            self.__err = value.absolute()
        else:
            self.__err = value
        return self

    def inherit(self) -> Self:
        """
        Inherit stdin, stdout and stderr from current process.

        Returns:
            self for chaining.
        """
        self.__in = sys.stdin
        self.inherit_out()
        return self

    def inherit_out(self, if_unset: bool = False) -> Self:
        """
        Inherit stdout and stderr from current process.

        Args:
            if_unset: when True, only inherit a stream if it is not already configured.

        Returns:
            self for chaining.
        """
        if not if_unset or self.__out is None:
            self.__out = sys.stdout
        if not if_unset or self.__err is None:
            self.__err = sys.stderr
        return self

    def timeout(self, timeout: int) -> Self:
        """Set a timeout for process to spawn."""
        self.__timeout = timeout
        return self

    def binary_path(self) -> Path:
        """
        Returns:
            path where binary is installed.
        """
        return Path(self.__cmd_path)

    def add_observer(self, observer: ProcessObserver) -> Self:
        """Register an observer to be notified on process spawn and exit."""
        self.__observers.append(observer)
        return self

    def copy(self, cmd: "Command") -> Self:
        """Copy all parameters from another command."""
        self.__env = cmd.__env.copy()
        self.__cwd = cmd.__cwd
        self.__args = cmd.__args
        self.__in = cmd.__in
        self.__out = cmd.__out
        self.__err = cmd.__err
        self.__timeout = cmd.__timeout
        self.__observers = list(cmd.__observers)
        return self

    def __prepare_stdin(self) -> int | None:
        """Convert the configured stdin redirect into a file descriptor or subprocess constant.

        Returns:
            fileno of a TextIO redirect, subprocess.PIPE for Queue or str input,
            or subprocess.DEVNULL when no stdin is configured.
        """
        if self.__in is None:
            return subprocess.DEVNULL
        if isinstance(self.__in, (queue.Queue, asyncio.Queue, str)):
            return subprocess.PIPE
        try:
            return self.__in.fileno()
        except (OSError, AttributeError):
            return subprocess.DEVNULL

    @staticmethod
    def __prepare_redirect(
        redirect: RedirectOut | None,
    ) -> int | IO[bytes]:
        """Prepare a stdout/stderr redirect for subprocess.

        Args:
            redirect: the redirect target.
        """
        if redirect is None:
            return subprocess.DEVNULL
        if isinstance(redirect, Path):
            return redirect.open("wb")  # noqa: SIM115
        if isinstance(redirect, TextIOBase):
            try:
                return redirect.fileno()
            except (OSError, AttributeError):
                pass
        # TextIOBase without a real fd (e.g. StringIO) and callables need PIPE for forwarding
        return subprocess.PIPE

    @staticmethod
    def __create_thread_forwarder(stream: IO[bytes], target: RedirectOut) -> Thread:
        """Create a thread that reads binary lines from stream, decodes them, and dispatches to target.

        Args:
            stream: binary readable stream to read from (e.g. process stdout or stderr pipe).
            target: destination to write decoded lines to; either a callable accepting a str,
                    or a TextIOBase instance (e.g. StringIO).
        Returns:
            daemon thread, not yet started.
        """

        def forward() -> None:
            try:
                for line in iter(stream.readline, b""):
                    decoded = line.decode(errors="replace")
                    if callable(target):
                        target(decoded)
                    elif isinstance(target, TextIOBase):
                        target.write(decoded)
            except (OSError, ValueError):
                pass
            finally:
                stream.close()

        return Thread(target=forward, daemon=True)

    def __handle_stdin(self, process: SyncProcess) -> Thread | None:
        """Handle stdin input from Queue or string.

        Args:
            process: the subprocess to feed stdin to.
        Returns:
            thread for async stdin feeding from Queue, or None for string input.
        """
        if isinstance(self.__in, queue.Queue):
            sync_queue = self.__in

            def forward() -> None:
                assert process.stdin is not None
                while True:
                    data = sync_queue.get()
                    if data is None:
                        break
                    encoded: bytes = data if isinstance(data, bytes) else data.encode()
                    process.stdin.write(encoded)
                    process.stdin.flush()
                process.stdin.close()

            thread = threading.Thread(target=forward, daemon=True)
            thread.start()
            return thread
        elif isinstance(self.__in, str):
            if process.stdin is not None:
                process.stdin.write(self.__in.encode())
                process.stdin.close()
        return None

    def __handle_stdout_stderr(self, process: SyncProcess) -> list[Thread]:
        """Start forwarding threads for stdout and stderr.

        Args:
            process: the subprocess to forward output from.
        Returns:
            list of threads started for output forwarding.
        """
        threads: list[Thread] = []

        for proc_stream, redirect in (
            (process.stdout, self.__out),
            (process.stderr, self.__err),
        ):
            if (
                _needs_forwarding(redirect)
                and proc_stream is not None
                and redirect is not None
            ):
                thread = self.__create_thread_forwarder(proc_stream, redirect)
                thread.start()
                threads.append(thread)
        return threads

    def __create_io_threads(
        self,
        process: SyncProcess,
    ) -> list[Thread]:
        """Start threads for forwarding I/O to callbacks/StringIO."""
        threads: list[Thread] = []

        # Handle stdin from Queue or string
        stdin_thread = self.__handle_stdin(process)
        if stdin_thread is not None:
            threads.append(stdin_thread)

        # Start forwarding threads for stdout and stderr
        threads.extend(self.__handle_stdout_stderr(process))
        return threads

    @staticmethod
    def __create_gc_thread(
        process: SyncProcess,
        io_threads: list[Thread],
        opened_files: list[IO[bytes]],
        observers: "list[ProcessObserver]",
    ) -> None:
        """Start a background thread to clean up resources after process exits.

        Args:
            process: the subprocess to monitor.
            io_threads: list of I/O forwarding threads to join.
            opened_files: list of file handles to close.
            observers: observers to notify once the process has exited.
        """

        def garbage_collector() -> None:
            try:
                process.wait()
            except OSError:
                pass
            finally:
                for th in io_threads:
                    with suppress(RuntimeError):
                        th.join(timeout=5)
                close(opened_files)
                for obs in observers:
                    obs.on_exit(process)

        thread = threading.Thread(target=garbage_collector, daemon=True)
        thread.start()

    def exec(self, wait_completion: bool = True) -> SyncProcess:
        """
        Run the command and return the subprocess.Popen directly.

        Args:
            wait_completion: wait for completion.
        Returns:
            subprocess.Popen process.
        """
        full_cmd = f"{self.__cmd_path} {' '.join(self.__args)}"

        stdin = self.__prepare_stdin()
        stdout = self.__prepare_redirect(self.__out)
        stderr = self.__prepare_redirect(self.__err)

        # Track open files
        opened_files: list[IO[bytes]] = []
        if not isinstance(stdout, int):
            opened_files.append(stdout)
        if not isinstance(stderr, int):
            opened_files.append(stderr)

        try:
            process = subprocess.Popen(
                [self.__cmd_path, *self.__args],
                env=self.__env if self.__env else None,
                cwd=self.__cwd.as_posix(),
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                bufsize=0,  # Unbuffered binary mode
            )
        except FileNotFoundError as err:
            close(opened_files)
            raise CommandNotFound(self.__cmd_path) from err

        # Start background threads to forward piped stdout/stderr to their targets
        io_threads = self.__create_io_threads(process)

        for obs in self.__observers:
            obs.on_spawn(process)

        if wait_completion:
            try:
                try:
                    process.wait(timeout=self.__timeout)
                except subprocess.TimeoutExpired as err:
                    raise TimeoutException(timeout=err.timeout, cmd=full_cmd) from err

                # Wait for I/O threads
                for io_thread in io_threads:
                    io_thread.join()

                # Raise exception on non-zero exit code
                if process.returncode is not None and process.returncode != 0:
                    raise ErrorReturnCode(cmd=full_cmd, exit_code=process.returncode)
                return process
            finally:
                close(opened_files)
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                for obs in self.__observers:
                    obs.on_exit(process)
        else:
            self.__create_gc_thread(process, io_threads, opened_files, self.__observers)
            return process

    async def __prepare_async_redirect(
        self,
        redirect: RedirectOut | None,
    ) -> int | IO[bytes]:
        """Prepare stdout/stderr redirect for async subprocess.

        Args:
            redirect: the redirect target.
        Returns:
            file descriptor or subprocess constant for the redirect.
        """
        if redirect is None:
            return subprocess.DEVNULL
        elif isinstance(redirect, Path):
            return await asyncio.to_thread(open, redirect, "wb")
        elif isinstance(redirect, TextIOBase):
            try:
                return redirect.fileno()
            except (OSError, AttributeError):
                return subprocess.PIPE
        elif _needs_forwarding(redirect):
            return subprocess.PIPE
        return subprocess.DEVNULL

    @staticmethod
    def __create_task_forwarder(
        stream: asyncio.StreamReader,
        target: RedirectOut,
    ) -> Task[None]:
        """Forward an async stream to configured target.

        Args:
            stream: the stream reader to forward from (stdout or stderr).
            target: the target destination (callable or TextIOBase).
        """

        async def forward() -> None:
            try:
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    decoded = line.decode(errors="replace")
                    if callable(target):
                        target(decoded)
                    elif isinstance(target, TextIOBase):
                        target.write(decoded)
            except (OSError, ValueError):
                pass

        return create_task(forward())

    async def __async_handle_stdin(self, process: "AsyncProcess") -> Task[None] | None:
        """Handle async stdin input from Queue or string.

        Args:
            process: the asyncio subprocess to feed stdin to.
        """
        if isinstance(self.__in, asyncio.Queue) and process.stdin is not None:
            async_queue = self.__in

            async def forward() -> None:
                assert process.stdin is not None
                while True:
                    data = await async_queue.get()
                    if data is None:
                        break
                    encoded: bytes = data if isinstance(data, bytes) else data.encode()
                    process.stdin.write(encoded)
                    await process.stdin.drain()
                process.stdin.close()
                await process.stdin.wait_closed()

            return asyncio.create_task(forward())
        elif isinstance(self.__in, str) and process.stdin is not None:
            process.stdin.write(self.__in.encode())
            await process.stdin.drain()
            process.stdin.close()
            await process.stdin.wait_closed()
        return None

    def __async_handle_stdout_stderr(
        self,
        process: "AsyncProcess",
    ) -> list[Task[None]]:
        """Create forwarding tasks for stdout and stderr.

        Args:
            process: the asyncio subprocess to forward output from.
        Returns:
            list of tasks started for output forwarding.
        """
        tasks: list[Task[None]] = []

        for proc_stream, redirect in (
            (process.stdout, self.__out),
            (process.stderr, self.__err),
        ):
            if (
                _needs_forwarding(redirect)
                and proc_stream is not None
                and redirect is not None
            ):
                tasks.append(self.__create_task_forwarder(proc_stream, redirect))
        return tasks

    async def __create_io_tasks(
        self,
        process: "AsyncProcess",
    ) -> list[asyncio.Task[None]]:
        """Start tasks for forwarding I/O to callbacks/StringIO.

        Args:
            process: the asyncio subprocess to forward I/O for.
        Returns:
            list of tasks started for I/O forwarding.
        """
        tasks: list[asyncio.Task[None]] = []

        # Handle stdin from Queue or string
        stdin_task = await self.__async_handle_stdin(process)
        if stdin_task is not None:
            tasks.append(stdin_task)

        # Start forwarding tasks for stdout and stderr
        tasks.extend(self.__async_handle_stdout_stderr(process))
        return tasks

    @staticmethod
    def __create_gc_task(
        process: "AsyncProcess",
        io_tasks: list[asyncio.Task[None]],
        opened_files: list[IO[bytes]],
        observers: "list[ProcessObserver]",
    ) -> None:
        """Clean up resources after async process completes.

        Args:
            process: the asyncio subprocess to wait for.
            io_tasks: list of I/O forwarding tasks to await.
            opened_files: list of file handles to close.
            observers: observers to notify once the process has exited.
        """

        async def garbage_collector() -> None:
            try:
                await process.wait()
            except OSError:
                pass
            finally:
                await gather(
                    *(wait_for(task, timeout=5) for task in io_tasks),
                    return_exceptions=True,
                )
                close(opened_files)
                for obs in observers:
                    obs.on_exit(process)

        create_task(garbage_collector())

    async def aexec(self, wait_completion: bool = True) -> "AsyncProcess":
        """
        Run the command asynchronously and return asyncio.subprocess.Process directly.

        Args:
            wait_completion: wait for completion.
        Returns:
            asyncio.subprocess.Process.
        """
        full_cmd = f"{self.__cmd_path} {' '.join(self.__args)}"

        # Prepare subprocess args for async
        stdin = self.__prepare_stdin()
        stdout, stderr = await gather(
            self.__prepare_async_redirect(self.__out),
            self.__prepare_async_redirect(self.__err),
        )

        # Track open files
        opened_files: list[IO[bytes]] = []
        if not isinstance(stdout, int):
            opened_files.append(stdout)
        if not isinstance(stderr, int):
            opened_files.append(stderr)

        try:
            process = await asyncio.create_subprocess_exec(
                self.__cmd_path,
                *self.__args,
                env=self.__env if self.__env else None,
                cwd=self.__cwd.as_posix(),
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
            )
        except FileNotFoundError as err:
            close(opened_files)
            raise CommandNotFound(self.__cmd_path) from err

        # Handle I/O concurrently with waiting
        io_tasks = await self.__create_io_tasks(process)

        for obs in self.__observers:
            obs.on_spawn(process)

        if wait_completion:
            try:
                try:
                    await asyncio.wait_for(process.wait(), timeout=self.__timeout)
                except asyncio.TimeoutError as err:
                    assert self.__timeout is not None
                    raise TimeoutException(
                        timeout=self.__timeout, cmd=full_cmd
                    ) from err

                # Wait for I/O tasks
                await gather(*io_tasks)

                # Raise exception on non-zero exit code
                if process.returncode is not None and process.returncode != 0:
                    raise ErrorReturnCode(cmd=full_cmd, exit_code=process.returncode)
                return process
            except asyncio.CancelledError:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=10)
                    except asyncio.TimeoutError:
                        process.kill()
                raise
            finally:
                close(opened_files)
                for obs in self.__observers:
                    obs.on_exit(process)
        else:
            self.__create_gc_task(process, io_tasks, opened_files, self.__observers)
            return process


class Bind:
    """Wrapper to create bind tied to command for process execution."""

    def __init__(self, cmd_path: str | Path) -> None:
        """
        Init bind.

        Args:
            cmd_path: command path.
        """
        self._cmd: Command = self.create(cmd_path)

    def create(self, cmd_path: str | Path) -> Command:
        """
        Create command wrapper for process execution.

        Args:
            cmd_path: command name or path.
        Returns:
            command wrapper.
        """
        return Command(cmd_path)

    def add_observer(self, observer: ProcessObserver) -> Self:
        """Register an observer to be notified on process spawn and exit."""
        self._cmd.add_observer(observer)
        return self

    @overload
    def env(self) -> dict[str, str]: ...

    @overload
    def env(self, value: dict[str, str]) -> Self: ...

    def env(self, value: dict[str, str] | None = None) -> dict[str, str] | Self:
        """
        Returns current env vars if no value is provided, otherwise set env vars.

        Args:
            value: env vars to set.
        Returns:
            current env vars if no value is provided or self for chaining.
        """
        if value is None:
            return self._cmd.env()
        self._cmd.env(value)
        return self

    @overload
    def cwd(self) -> Path: ...

    @overload
    def cwd(self, value: Path) -> Self: ...

    def cwd(self, value: Path | None = None) -> Path | Self:
        """
        Returns current cwd if no value is provided, otherwise set cwd.

        Args:
            value: cwd to set.
        Returns:
            current cwd if no value is provided or self for chaining.
        """
        if value is None:
            return self._cmd.cwd()
        self._cmd.cwd(value)
        return self

    def stdin(self, value: RedirectIn) -> Self:
        """
        Set stdin redirect.

        Args:
            value: stdin to set.
        Returns:
            self for chaining.
        """
        self._cmd.stdin(value)
        return self

    def stdout(self, value: RedirectOut) -> Self:
        """
        Set stdout redirect.

        Args:
            value: stdout to set.
        Returns:
            self for chaining.
        """
        self._cmd.stdout(value)
        return self

    def stderr(self, value: RedirectOut) -> Self:
        """
        Set stderr redirect.

        Args:
            value: stderr to set.
        Returns:
            self for chaining.
        """
        self._cmd.stderr(value)
        return self

    def inherit(self) -> Self:
        """
        Inherit stdin, stdout and stderr from current process.

        Returns:
            self for chaining.
        """
        self._cmd.inherit()
        return self

    def inherit_out(self, if_unset: bool = False) -> Self:
        """
        Inherit stdout and stderr from current process.

        Args:
            if_unset: when True, only inherit a stream if it is not already configured.

        Returns:
            self for chaining.
        """
        self._cmd.inherit_out(if_unset=if_unset)
        return self

    def timeout(self, timeout: int) -> Self:
        """Set a timeout for process to spawn."""
        self._cmd.timeout(timeout)
        return self

    def binary_path(self) -> Path:
        """
        Returns:
            path where binary is installed.
        """
        return self._cmd.binary_path()

    def copy(self, cmd: Command) -> Self:
        """Copy all parameters from another command."""
        self._cmd.copy(cmd)
        return self
