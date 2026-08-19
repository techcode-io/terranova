from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path
from typing import cast

import pytest

from terranova.utils import SharedContext

_FAKE_TERRAFORM_SCRIPT = """#!/usr/bin/env python3
import base64
import json
import os
import sys

config_path = os.environ["TERRANOVA_TEST_CONFIG"]
capture_path = os.environ["TERRANOVA_TEST_CAPTURE"]

with open(capture_path, "w") as f:
    json.dump({"argv": sys.argv[1:], "env": dict(os.environ)}, f)

config: dict = {}
if os.path.exists(config_path):
    with open(config_path) as f:
        config = json.load(f)

# Mirror `terraform plan -out=<path>` writing a plan file, if configured.
out_bytes_b64 = config.get("out_bytes_b64")
if out_bytes_b64:
    for arg in sys.argv[1:]:
        if arg.startswith("-out="):
            with open(arg[len("-out="):], "wb") as out_f:
                out_f.write(base64.b64decode(out_bytes_b64))

stdout = config.get("stdout", "")
if stdout:
    sys.stdout.write(stdout)
sys.stdout.flush()

sys.exit(config.get("exit_code", 0))
"""


@pytest.fixture(autouse=True)
def _reset_shared_context(  # pyright: ignore[reportUnusedFunction]
    tmp_path: Path,
) -> None:
    SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)


class FakeTerraform:
    """Test double for the `terraform` binary.

    Backed by a real executable script (in the spirit of tests/it/test_process.py
    swapping in "echo") so tests exercise real subprocess plumbing while still
    getting deterministic exit codes/stdout and captured argv/env.
    """

    def __init__(self, config_path: Path, capture_path: Path) -> None:
        self._config_path: Path = config_path
        self._capture_path: Path = capture_path

    def set_exit_code(self, code: int) -> None:
        self._update_config("exit_code", code)

    def set_stdout(self, text: str) -> None:
        self._update_config("stdout", text)

    def set_out_bytes(self, data: bytes) -> None:
        """Configure the fake `-out=<path>` plan file content, base64-encoded."""
        self._update_config("out_bytes_b64", base64.b64encode(data).decode("ascii"))

    def _update_config(self, key: str, value: int | str) -> None:
        config = cast("dict[str, int | str]", json.loads(self._config_path.read_text()))
        config[key] = value
        self._config_path.write_text(json.dumps(config))

    @property
    def captured_argv(self) -> list[str]:
        capture = cast("dict[str, object]", json.loads(self._capture_path.read_text()))
        return cast("list[str]", capture["argv"])

    @property
    def captured_env(self) -> dict[str, str]:
        capture = cast("dict[str, object]", json.loads(self._capture_path.read_text()))
        return cast("dict[str, str]", capture["env"])

    @property
    def was_invoked(self) -> bool:
        return self._capture_path.exists()


@pytest.fixture
def fake_terraform_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> FakeTerraform:
    bin_dir = tmp_path / "fake_bin"
    bin_dir.mkdir(exist_ok=True)
    script_path = bin_dir / "terraform"
    script_path.write_text(_FAKE_TERRAFORM_SCRIPT)
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

    config_path = tmp_path / "fake_terraform_config.json"
    config_path.write_text("{}")
    capture_path = tmp_path / "fake_terraform_capture.json"

    monkeypatch.setenv("TERRANOVA_TEST_CONFIG", str(config_path))
    monkeypatch.setenv("TERRANOVA_TEST_CAPTURE", str(capture_path))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    return FakeTerraform(config_path, capture_path)
