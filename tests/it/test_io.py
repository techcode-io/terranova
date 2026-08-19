from __future__ import annotations

import pytest

from terranova.io import close


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
