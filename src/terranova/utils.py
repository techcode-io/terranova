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
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn, dataclass_transform, override

from click.exceptions import Exit
from rich.console import Console, RenderableType
from serde import disabled, field
from serde import serde as inner_serde

from terranova.exceptions import ExplainedError


@dataclass_transform(field_specifiers=(field,))
def serde[T](cls: type[T]) -> type[T]:
    """Shorthand for serde with type check disabled."""
    return inner_serde(cls, type_check=disabled)


def int_or_default(value: object, default: int) -> int:
    """Coerce an untyped value (e.g. from `json.loads()`) to `int`, or `default`."""
    return value if isinstance(value, int) else default


def str_or_none(value: object) -> str | None:
    """Coerce an untyped value (e.g. from `json.loads()`) to `str`, or `None`."""
    return value if isinstance(value, str) else None


class Constants:
    """All constants"""

    ENCODING_UTF_8: Final[str] = "utf-8"
    FILE_MODE_READ: Final[str] = "r"
    MANIFEST_FILE_NAME: Final[str] = "manifest.yml"


class Log(ABC):
    """Interface for logging an action/success/failure using a common pattern."""

    @property
    @abstractmethod
    def console(self) -> Console:
        """Console used for standard output (e.g. to host a live display)."""

    @abstractmethod
    def action(self, msg: object) -> None:
        """Log an action."""

    @abstractmethod
    def success(self, msg: object) -> None:
        """Log a success."""

    @abstractmethod
    def failure(self, msgs: str | list[str], err: Exception | None = None) -> None:
        """Log a failure."""

    @abstractmethod
    def render(self, renderable: RenderableType, err: bool = False) -> None:
        """Render any `rich` component (table, panel, text...)."""

    def fatal(
        self, msgs: str | list[str], err: Exception | None = None, raise_exit: int = 1
    ) -> NoReturn:
        """Log a failure and exit."""
        self.failure(msgs, err)
        raise Exit(code=raise_exit)


class ConsoleLog(Log):
    """`Log` implementation backed by a pair of `rich` consoles."""

    def __init__(self, debug: bool = False) -> None:
        """Init console log."""
        self.__console = Console()
        self.__err_console = Console(stderr=True)
        self.__debug = debug

    def configure(self, debug: bool) -> None:
        """Set debug mode (prints tracebacks on failure)."""
        self.__debug = debug

    @property
    @override
    def console(self) -> Console:
        """Console used for standard output."""
        return self.__console

    @override
    def action(self, msg: object) -> None:
        """Log an action."""
        self.__console.print(f"[yellow]⇒[/yellow] {msg}")

    @override
    def success(self, msg: object) -> None:
        """Log a success."""
        self.__console.print(f"[green]✓[/green] Succeeded to {msg}")

    @override
    def render(self, renderable: RenderableType, err: bool = False) -> None:
        """Render any `rich` component to stdout (or stderr if `err`)."""
        if err:
            self.__err_console.print(renderable)
        else:
            self.__console.print(renderable)

    @override
    def failure(self, msgs: str | list[str], err: Exception | None = None) -> None:
        """Log a failure."""
        err_console = self.__err_console
        if self.__debug and err:
            err_console.print_exception()
            err_console.print(err)
        if not isinstance(msgs, list):
            msgs = [msgs]
        err_console.print(f"[red]x[/red] Failed to {msgs[0]!s}")
        for msg in msgs[1:]:
            err_console.print(f"  {msg!s}")

        # Render explained error
        if err:
            if isinstance(err, ExplainedError):
                err_console.print(f"  Cause: {err.cause}")
                if err.resolution:
                    err_console.print(f"  Resolution: {err.resolution}")
            else:
                err_console.print(f"  Details: {err}")


@dataclass(frozen=True)
class AppContext:
    """
    Immutable per-invocation application context, attached to the Click context as `ctx.obj`.

    Only Click command handlers read it; everything below them receives
    explicit arguments (paths, flags).
    """

    conf_dir: Path
    verbose: bool = False

    @property
    def resources_dir(self) -> Path:
        """Returns resources directory path."""
        return self.conf_dir / "resources"

    @property
    def shared_dir(self) -> Path:
        """Returns shared directory path."""
        return self.conf_dir / "shared"

    @property
    def terraform_shared_dir(self) -> Path:
        """Returns terraform shared directory path."""
        return self.conf_dir / ".terraform"

    @property
    def terraform_shared_states_dir(self) -> Path:
        """Returns terraform shared states directory path."""
        return self.terraform_shared_dir / "states"

    @property
    def terraform_shared_plugin_cache_dir(self) -> Path:
        """Returns terraform shared plugin cache directory path."""
        return self.terraform_shared_dir / "plugin-cache"


log: Final[ConsoleLog] = ConsoleLog()
