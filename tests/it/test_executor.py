from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import override

from terranova.executor import ParallelExecutor, ResourceGroupTask, SequentialExecutor
from terranova.process import ErrorReturnCode
from terranova.utils import AppContext

_CTX = AppContext.create(debug=False, verbose=False, conf_dir=Path("."))


class _FakeTask(ResourceGroupTask):
    def __init__(self, rel_path: str, action: Callable[[], None]) -> None:
        super().__init__(_CTX, full_path=Path(rel_path), rel_path=rel_path)
        self._action: Callable[[], None] = action

    @override
    def run(self) -> None:
        self._action()


def _task(rel_path: str, action: Callable[[], None]) -> ResourceGroupTask:
    return _FakeTask(rel_path, action)


def _ok() -> None:
    return None


def _fail(rel_path: str = "boom") -> None:
    raise ErrorReturnCode(cmd=rel_path, exit_code=1)


class TestSequentialExecutor:
    def test_all_succeed(self) -> None:
        tasks = [_task("a", _ok), _task("b", _ok), _task("c", _ok)]
        waves = [["a"], ["b"], ["c"]]
        results = SequentialExecutor().run(tasks, fail_at_end=False, waves=waves)
        assert [r.status for r in results] == ["succeeded", "succeeded", "succeeded"]

    def test_stops_at_first_failure_without_fail_at_end(self) -> None:
        tasks = [_task("a", _ok), _task("b", _fail), _task("c", _ok)]
        waves = [["a"], ["b"], ["c"]]
        results = SequentialExecutor().run(tasks, fail_at_end=False, waves=waves)
        assert [r.rel_path for r in results] == ["a", "b"]
        assert results[-1].status == "failed"

    def test_continues_past_failure_with_fail_at_end(self) -> None:
        tasks = [_task("a", _ok), _task("b", _fail), _task("c", _ok)]
        waves = [["a"], ["b"], ["c"]]
        results = SequentialExecutor().run(tasks, fail_at_end=True, waves=waves)
        assert [r.rel_path for r in results] == ["a", "b", "c"]
        assert [r.status for r in results] == ["succeeded", "failed", "succeeded"]

    def test_executes_in_flattened_wave_order_not_task_order(self) -> None:
        order: list[str] = []

        def make(rel_path: str) -> Callable[[], None]:
            def _run() -> None:
                order.append(rel_path)

            return _run

        # Tasks are provided out of dependency order; waves determine execution order.
        tasks = [
            _task("consumer", make("consumer")),
            _task("producer", make("producer")),
        ]
        waves = [["producer"], ["consumer"]]
        results = SequentialExecutor().run(tasks, fail_at_end=False, waves=waves)
        assert order == ["producer", "consumer"]
        assert [r.rel_path for r in results] == ["producer", "consumer"]

    def test_multi_project_wave_runs_in_wave_list_order(self) -> None:
        order: list[str] = []

        def make(rel_path: str) -> Callable[[], None]:
            def _run() -> None:
                order.append(rel_path)

            return _run

        tasks = [_task("a", make("a")), _task("b", make("b"))]
        waves = [["a", "b"]]
        SequentialExecutor().run(tasks, fail_at_end=False, waves=waves)
        assert order == ["a", "b"]


class TestParallelExecutor:
    def _executor(self, max_workers: int | None = None) -> ParallelExecutor:
        return ParallelExecutor(max_workers=max_workers)

    def test_wave_succeeds_then_next_wave_starts(self) -> None:
        completed: list[str] = []

        def make(rel_path: str) -> Callable[[], None]:
            def _run() -> None:
                completed.append(rel_path)

            return _run

        tasks = [_task("a", make("a")), _task("b", make("b"))]
        waves = [["a"], ["b"]]
        results = self._executor().run(tasks, fail_at_end=False, waves=waves)
        assert completed == ["a", "b"]
        assert all(r.status == "succeeded" for r in results)

    def test_mid_wave_failure_lets_siblings_finish_but_stops_next_wave(self) -> None:
        completed: set[str] = set()
        lock = threading.Lock()

        def slow_ok(rel_path: str) -> Callable[[], None]:
            def _run() -> None:
                time.sleep(0.05)
                with lock:
                    completed.add(rel_path)

            return _run

        def fast_fail() -> None:
            raise ErrorReturnCode(cmd="b", exit_code=1)

        tasks = [
            _task("a", slow_ok("a")),
            _task("b", fast_fail),
            _task("c", slow_ok("c")),
        ]
        waves = [["a", "b", "c"]]
        results = self._executor().run(tasks, fail_at_end=False, waves=waves)

        # both siblings of the failing task still ran to completion
        assert completed == {"a", "c"}
        assert {r.rel_path for r in results} == {"a", "b", "c"}
        assert next(r for r in results if r.rel_path == "b").status == "failed"

    def test_fail_at_end_still_runs_subsequent_waves(self) -> None:
        completed: list[str] = []

        def make(rel_path: str) -> Callable[[], None]:
            def _run() -> None:
                completed.append(rel_path)

            return _run

        def fail() -> None:
            raise ErrorReturnCode(cmd="a", exit_code=1)

        tasks = [_task("a", fail), _task("b", make("b"))]
        waves = [["a"], ["b"]]
        results = self._executor().run(tasks, fail_at_end=True, waves=waves)
        assert completed == ["b"]
        statuses = {r.rel_path: r.status for r in results}
        assert statuses == {"a": "failed", "b": "succeeded"}

    def test_no_fail_at_end_stops_before_next_wave(self) -> None:
        completed: list[str] = []

        def make(rel_path: str) -> Callable[[], None]:
            def _run() -> None:
                completed.append(rel_path)

            return _run

        def fail() -> None:
            raise ErrorReturnCode(cmd="a", exit_code=1)

        tasks = [_task("a", fail), _task("b", make("b"))]
        waves = [["a"], ["b"]]
        results = self._executor().run(tasks, fail_at_end=False, waves=waves)
        assert completed == []
        assert [r.rel_path for r in results] == ["a"]

    def test_results_ordered_by_completion_not_submission(self) -> None:
        order: list[str] = []
        lock = threading.Lock()

        def make(rel_path: str, delay: float) -> Callable[[], None]:
            def _run() -> None:
                time.sleep(delay)
                with lock:
                    order.append(rel_path)

            return _run

        # "slow" is submitted first but finishes last.
        tasks = [_task("slow", make("slow", 0.08)), _task("fast", make("fast", 0.0))]
        waves = [["slow", "fast"]]
        results = self._executor().run(tasks, fail_at_end=False, waves=waves)
        assert order == ["fast", "slow"]
        assert [r.rel_path for r in results] == ["fast", "slow"]

    def test_max_workers_caps_concurrency(self) -> None:
        peak = 0
        current = 0
        lock = threading.Lock()

        def make() -> Callable[[], None]:
            def _run() -> None:
                nonlocal peak, current
                with lock:
                    current += 1
                    peak = max(peak, current)
                time.sleep(0.03)
                with lock:
                    current -= 1

            return _run

        tasks = [_task(str(i), make()) for i in range(6)]
        waves = [[str(i) for i in range(6)]]
        self._executor(max_workers=2).run(tasks, fail_at_end=False, waves=waves)
        assert peak <= 2

    def test_empty_waves_returns_no_results(self) -> None:
        tasks = [_task("a", _ok)]
        results = self._executor().run(tasks, fail_at_end=False, waves=[])
        assert results == []
