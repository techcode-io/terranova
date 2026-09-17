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
from rich.console import Console
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

    @abstractmethod
    def action(self, msg: object) -> None:
        """Log an action."""

    @abstractmethod
    def success(self, msg: object) -> None:
        """Log a success."""

    @abstractmethod
    def failure(self, msgs: str | list[str], err: Exception | None = None) -> None:
        """Log a failure."""

    def fatal(
        self, msgs: str | list[str], err: Exception | None = None, raise_exit: int = 1
    ) -> NoReturn:
        """Log a failure and exit."""
        self.failure(msgs, err)
        raise Exit(code=raise_exit)


class ConsoleLog(Log):
    """`Log` implementation backed by a pair of `rich` consoles."""

    def __init__(self, console: Console, err_console: Console, debug: bool) -> None:
        """Init console log."""
        self.__console = console
        self.__err_console = err_console
        self.__debug = debug

    @override
    def action(self, msg: object) -> None:
        """Log an action."""
        self.__console.print(f"[yellow]⇒[/yellow] {msg}")

    @override
    def success(self, msg: object) -> None:
        """Log a success."""
        self.__console.print(f"[green]✓[/green] Succeeded to {msg}")

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
    """Per-invocation application context, attached to the Click context as `ctx.obj`."""

    console: Console
    err_console: Console
    debug: bool
    verbose: bool
    conf_dir: Path
    log: Log

    @staticmethod
    def create(debug: bool, verbose: bool, conf_dir: Path) -> "AppContext":
        """Build a fresh application context."""
        console = Console()
        err_console = Console(stderr=True)
        return AppContext(
            console=console,
            err_console=err_console,
            debug=debug,
            verbose=verbose,
            conf_dir=conf_dir,
            log=ConsoleLog(console, err_console, debug),
        )

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
