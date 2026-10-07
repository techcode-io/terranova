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
import os
import tempfile
from collections.abc import Generator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Protocol


class Closeable(Protocol):
    """Protocol for objects that support closing."""

    def close(self) -> None: ...


def close(files: Sequence[Closeable]) -> None:
    """
    Close a list of file handles, ignoring any errors.

    Args:
        files: list of file handles to close.
    """
    for file in files:
        with suppress(OSError):
            file.close()


@contextmanager
def temp_file(prefix: str = "terranova-", content: bytes = b"") -> Generator[Path]:
    """
    Provide the path of a temporary file holding `content`, removed on leaving the block.

    The file is closed (but kept) before it is yielded, so another process can open it:
    Windows forbids opening a file that another handle still holds open.

    Args:
        prefix: prefix of the temporary file name.
        content: initial binary content.

    Yields:
        path of the temporary file.
    """
    with tempfile.NamedTemporaryFile(prefix=prefix, delete_on_close=False) as file:
        file.write(content)
        file.close()
        yield Path(file.name)


def write_atomic(path: Path, content: str) -> None:
    """
    Replace an existing file with `content` atomically, keeping its permissions.

    The content is written to a temporary file next to `path`, then moved over
    it, so readers never see a partially written file.

    Args:
        path: existing file to replace.
        content: new UTF-8 text content.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(content)
        os.chmod(tmp_name, path.stat().st_mode & 0o7777)
        os.replace(tmp_name, path)
    except BaseException:
        os.unlink(tmp_name)
        raise
