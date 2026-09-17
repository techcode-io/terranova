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
from abc import ABC, abstractmethod
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, override

from terranova.graph import Wave, normalize_rel_path
from terranova.process import ErrorReturnCode
from terranova.utils import AppContext


class ResourceGroupTask(ABC):
    """One resource group's unit of work for an executor. Subclasses implement `run()`."""

    def __init__(
        self, ctx: AppContext, full_path: Path, rel_path: str, quiet: bool = False
    ) -> None:
        """
        Init project task.

        Args:
            ctx: the application context.
            full_path: absolute path to the resource group's directory.
            rel_path: the resource group's path relative to the resources dir.
            quiet: when True, the task should suppress its own per-project
                   logging (start/success messages) - set by callers running
                   many groups concurrently, where a live status display
                   already shows what's running and per-project chatter would
                   just be noise. Failures should still be reported regardless.
        """
        self.ctx: AppContext = ctx
        self.full_path: Path = full_path
        self.rel_path: str = rel_path
        self.quiet: bool = quiet

    @abstractmethod
    def run(self) -> None:
        """Execute this task, raising `ErrorReturnCode` on failure."""


@dataclass(frozen=True)
class ResourceGroupResult:
    """Outcome of running one resource group's `ResourceGroupTask`."""

    rel_path: str
    status: Literal["succeeded", "failed"]
    error: ErrorReturnCode | None = None

    @property
    def exit_code(self) -> int | None:
        """Exit code of the underlying terraform invocation, if it failed."""
        return self.error.exit_code if self.error else None


class Executor(ABC):
    """
    Abstraction over how a set of resource group tasks gets run.

    Both executors are driven by the same dependency waves - topologically
    ordered groups of rel_paths where every group's dependencies were satisfied
    by an earlier group. `SequentialExecutor` runs the waves flattened, one
    project at a time; `ParallelExecutor` runs each wave's projects concurrently.
    Waves are always required, even for a single dependency-free project (in
    which case there's exactly one wave holding it), so dependency ordering is
    validated and honored regardless of which executor is selected.
    """

    @abstractmethod
    def run(
        self,
        tasks: list[ResourceGroupTask],
        fail_at_end: bool,
        waves: list[Wave],
    ) -> list[ResourceGroupResult]:
        """
        Run every task and return their results.

        Args:
            tasks: every task available to run, keyed internally by rel_path.
            fail_at_end: if True, keep going after a failure instead of stopping early.
            waves: precomputed topological waves (lists of rel_paths) that determine
                   execution order/grouping.

        Returns:
            the result of every task that was actually run.
        """


class SequentialExecutor(Executor):
    """Runs every task one after another, in dependency (wave) order."""

    @override
    def run(
        self,
        tasks: list[ResourceGroupTask],
        fail_at_end: bool,
        waves: list[Wave],
    ) -> list[ResourceGroupResult]:
        tasks_by_rel_path = {normalize_rel_path(task.rel_path): task for task in tasks}
        results: list[ResourceGroupResult] = []
        for rel_path in (rel_path for wave in waves for rel_path in wave):
            task = tasks_by_rel_path[rel_path]
            try:
                task.run()
                results.append(
                    ResourceGroupResult(rel_path=task.rel_path, status="succeeded")
                )
            except ErrorReturnCode as err:
                results.append(
                    ResourceGroupResult(
                        rel_path=task.rel_path, status="failed", error=err
                    )
                )
                if not fail_at_end:
                    break
        return results


class ExecutorObserver(Protocol):
    """
    Notified of per-project lifecycle events during a `ParallelExecutor` run.

    Rendering (progress bars, colored output, ...) is the observer's concern,
    not the executor's - see `terranova.ui.ParallelProgress` for the concrete
    implementation used by the CLI. An executor only ever calls these two
    methods; it never renders anything itself.
    """

    def start(self, rel_path: str) -> None:
        """Called when a project's task has been submitted to run."""

    def finish(self, rel_path: str, succeeded: bool) -> None:
        """Called when a project's task has completed."""


class _NoopObserver:
    """No-op `ExecutorObserver`, used when the caller doesn't need feedback."""

    def start(self, rel_path: str) -> None:
        _ = rel_path

    def finish(self, rel_path: str, succeeded: bool) -> None:
        _ = (rel_path, succeeded)


class ParallelExecutor(Executor):
    """Runs tasks concurrently within each dependency wave, waiting for a wave to
    fully drain before starting the next one."""

    def __init__(
        self,
        max_workers: int | None = None,
        observer: ExecutorObserver | None = None,
    ) -> None:
        """Init the parallel executor."""
        self.__max_workers = max_workers
        self.__observer: ExecutorObserver = (
            observer if observer is not None else _NoopObserver()
        )

    @override
    def run(
        self,
        tasks: list[ResourceGroupTask],
        fail_at_end: bool,
        waves: list[Wave],
    ) -> list[ResourceGroupResult]:
        tasks_by_rel_path = {normalize_rel_path(task.rel_path): task for task in tasks}
        results: list[ResourceGroupResult] = []

        for wave in waves:
            if any(result.status == "failed" for result in results) and (
                not fail_at_end
            ):
                break

            wave_tasks = [tasks_by_rel_path[rel_path] for rel_path in wave]
            with ThreadPoolExecutor(max_workers=self.__max_workers) as executor:
                futures: dict[Future[None], ResourceGroupTask] = {
                    executor.submit(task.run): task for task in wave_tasks
                }
                for task in wave_tasks:
                    self.__observer.start(task.rel_path)

                for future in as_completed(futures):
                    task = futures[future]
                    try:
                        future.result()
                        results.append(
                            ResourceGroupResult(
                                rel_path=task.rel_path, status="succeeded"
                            )
                        )
                        self.__observer.finish(task.rel_path, succeeded=True)
                    except ErrorReturnCode as err:
                        results.append(
                            ResourceGroupResult(
                                rel_path=task.rel_path, status="failed", error=err
                            )
                        )
                        self.__observer.finish(task.rel_path, succeeded=False)

        return results


def create_executor(
    strategy: Literal["sequential", "parallel"],
    max_workers: int | None = None,
    observer: ExecutorObserver | None = None,
) -> Executor:
    """
    Build the executor for a given strategy name.

    Args:
        strategy: `"sequential"` or `"parallel"`, typically taken straight from
                  the `--strategy` CLI option.
        max_workers: for `"parallel"`, the maximum resource groups run
                     concurrently within a wave. Ignored for `"sequential"`.
        observer: for `"parallel"`, notified of per-project lifecycle events.
                  Ignored for `"sequential"`.

    Returns:
        a `SequentialExecutor` or `ParallelExecutor`.
    """
    match strategy:
        case "parallel":
            return ParallelExecutor(max_workers=max_workers, observer=observer)
        case "sequential":
            return SequentialExecutor()
