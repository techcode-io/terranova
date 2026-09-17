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
"""Terminal rendering for terranova. Keep presentation concerns here, out of
orchestration/execution logic (e.g. `terranova.executor`)."""

from types import TracebackType
from typing import Self

from rich.progress import Progress, SpinnerColumn, TaskID, TextColumn

from terranova.utils import SharedContext


class ParallelProgress:
    """
    Live status display for the parallel executor.

    Scales to a large number of resource groups by design: only currently
    *running* projects get their own row - bounded by `--group-concurrency`,
    not by the total number of resource groups - and a single overall row
    tracks the aggregate `completed/total` count. Rows disappear once a
    project finishes rather than piling up, and only failures are printed
    individually; successes are reflected in the overall count instead of
    one line per project.
    """

    def __init__(self, total: int) -> None:
        """Init the status UI for `total` resource groups."""
        self.__progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=SharedContext.console(),
            transient=False,
        )
        self.__total = total
        self.__completed = 0
        self.__overall: TaskID = self.__progress.add_task(self.__overall_description())
        self.__running: dict[str, TaskID] = {}

    def __overall_description(self) -> str:
        return f"Running {self.__completed}/{self.__total}"

    def __enter__(self) -> Self:
        self.__progress.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.__progress.__exit__(exc_type, exc_val, exc_tb)

    def start(self, rel_path: str) -> None:
        """Mark a project as running - adds its row to the live display."""
        task_id = self.__progress.add_task(rel_path, total=None)
        self.__running[rel_path] = task_id

    def finish(self, rel_path: str, succeeded: bool) -> None:
        """
        Mark a project as finished.

        Removes its row (rather than leaving it behind) and advances the
        overall count. Only failures print a standalone line - successes
        would just be noise at scale, and are already reflected in the
        overall count.
        """
        task_id = self.__running.pop(rel_path, None)
        if task_id is not None:
            self.__progress.remove_task(task_id)
        self.__completed += 1
        self.__progress.update(self.__overall, description=self.__overall_description())
        if not succeeded:
            self.print(f"[red]failed:[/red] {rel_path}")

    def print(self, message: str) -> None:
        """Print a message above the live status display."""
        self.__progress.console.print(message)
