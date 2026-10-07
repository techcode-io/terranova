from __future__ import annotations

import os
from pathlib import Path

import pytest

from terranova.io import close, temp_file, write_atomic


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
    @pytest.mark.posix_only
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


class TestTempFile:
    def test_yields_an_existing_file_with_the_content(self) -> None:
        with temp_file(content=b"plan-bytes") as path:
            assert path.read_bytes() == b"plan-bytes"

    def test_is_empty_by_default(self) -> None:
        with temp_file() as path:
            assert path.read_bytes() == b""

    def test_applies_the_prefix(self) -> None:
        with temp_file(prefix="custom-") as path:
            assert path.name.startswith("custom-")

    def test_file_can_be_reopened_while_in_the_block(self) -> None:
        # Another process (terraform) writes to it by name: no handle may stay open on it,
        # which Windows would refuse
        with temp_file() as path:
            path.write_bytes(b"written elsewhere")
            assert path.read_bytes() == b"written elsewhere"

    def test_is_removed_on_exit(self) -> None:
        with temp_file() as path:
            pass
        assert not path.exists()

    def test_is_removed_when_the_block_raises(self) -> None:
        with pytest.raises(RuntimeError), temp_file() as path:
            raise RuntimeError
        assert not path.exists()
