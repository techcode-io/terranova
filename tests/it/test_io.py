from __future__ import annotations

import os
from pathlib import Path

import pytest

from terranova.io import close, write_atomic


class _FakeCloseable:
    def __init__(self, exc: Exception | None = None) -> None:
        self.closed: bool = False
        self._exc: Exception | None = exc

    def close(self) -> None:
        self.closed = True
        if self._exc is not None:
            raise self._exc


class TestClose:
    def test_calls_close_on_all_files(self) -> None:
        files = [_FakeCloseable(), _FakeCloseable(), _FakeCloseable()]
        close(files)
        assert all(f.closed for f in files)

    def test_suppresses_oserror_on_one_file_continues_others(self) -> None:
        files = [
            _FakeCloseable(),
            _FakeCloseable(OSError("boom")),
            _FakeCloseable(),
        ]
        close(files)
        assert all(f.closed for f in files)

    def test_propagates_non_oserror_and_aborts_remaining_closes(self) -> None:
        files = [
            _FakeCloseable(),
            _FakeCloseable(ValueError("boom")),
            _FakeCloseable(),
        ]
        with pytest.raises(ValueError, match="boom"):
            close(files)
        assert files[0].closed is True
        assert files[1].closed is True
        assert files[2].closed is False


class TestWriteAtomic:
    def test_replaces_content_and_keeps_permissions(self, tmp_path: Path) -> None:
        target = tmp_path / "file.txt"
        target.write_text("old")
        target.chmod(0o640)
        write_atomic(target, "new")
        assert target.read_text() == "new"
        assert target.stat().st_mode & 0o777 == 0o640

    def test_leaves_no_temporary_file(self, tmp_path: Path) -> None:
        target = tmp_path / "file.txt"
        target.write_text("old")
        write_atomic(target, "new")
        assert os.listdir(tmp_path) == ["file.txt"]

    def test_failure_keeps_original_and_cleans_up(self, tmp_path: Path) -> None:
        target = tmp_path / "file.txt"
        target.write_text("old")
        with pytest.raises(UnicodeEncodeError):
            write_atomic(target, "\ud800")
        assert target.read_text() == "old"
        assert os.listdir(tmp_path) == ["file.txt"]
