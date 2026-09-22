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
import hashlib
import io
import platform
import stat
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest
import urllib3
from click.testing import CliRunner

from terranova import engines
from terranova.cli import main
from terranova.engines import EnginePlatform

LINUX = EnginePlatform("linux", "amd64")


class _Response:
    status: int
    data: bytes

    def __init__(self, status: int, data: bytes = b"") -> None:
        self.status = status
        self.data = data


class FakeHttp:
    """Replaces `urllib3.PoolManager.request`, recording requested URLs."""

    handler: Callable[[str], _Response]

    def __init__(self, handler: Callable[[str], _Response]) -> None:
        self.calls: list[str] = []
        self.handler = handler

    def request(self, method: str, url: str) -> _Response:
        assert method == "GET"
        self.calls.append(url)
        return self.handler(url)


def _zip_bytes(name: str = "terraform") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, "#!/bin/sh\n")
    return buf.getvalue()


def _release(engine_name: str = "terraform", digest: str | None = None) -> FakeHttp:
    """Serve a fake release, with the archive's real checksum unless overridden."""
    descriptor = engines.ENGINE_DESCRIPTORS[engine_name]
    archive = _zip_bytes(descriptor.binary_base_name)

    def handler(url: str) -> _Response:
        if url.endswith("SHA256SUMS"):
            name = url.rsplit("/", 1)[-1].replace("SHA256SUMS", "")
            version = name.removeprefix(f"{descriptor.binary_base_name}_").removesuffix(
                "_"
            )
            sha = digest or hashlib.sha256(archive).hexdigest()
            archive_name = descriptor.archive_name(version, LINUX)
            return _Response(200, f"{sha}  {archive_name}\n".encode())
        return _Response(200, archive)

    return FakeHttp(handler)


@pytest.fixture(autouse=True)
def _linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")


def _serve(monkeypatch: pytest.MonkeyPatch, http: FakeHttp) -> None:
    # A plain function on the class, so it receives the pool as `self` like the real method.
    def request(_pool: urllib3.PoolManager, method: str, url: str) -> _Response:
        return http.request(method, url)

    monkeypatch.setattr(urllib3.PoolManager, "request", request)


def _seed_installed(
    cache_dir: Path, engine_name: str, version: str, binary_name: str | None = None
) -> Path:
    """Manually seed a cached version directory, without going through `resolve()`."""
    name = binary_name or engines.ENGINE_DESCRIPTORS[engine_name].binary_base_name
    binary = cache_dir / engine_name / version / name
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"#!/bin/sh\n")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return binary


def _write_manifest_with_engine(
    conf_dir: Path, name: str, engine_name: str, version: str
) -> None:
    resource_dir = conf_dir / "resources" / name
    resource_dir.mkdir(parents=True)
    (resource_dir / "manifest.yml").write_text(f"""version: "1.4"
metadata:
  name: {name}
  description: test
engine:
  name: {engine_name}
  version: "{version}"
""")


class TestRuntimeLs:
    @pytest.mark.usefixtures("cache_dir")
    def test_empty_cache_lists_nothing(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main, args=["--conf-dir", str(tmp_path), "runtime", "ls"]
        )
        assert result.exit_code == 0
        assert "1.9.5" not in result.stdout

    def test_lists_installed_versions(
        self, runner: CliRunner, cache_dir: Path, tmp_path: Path
    ) -> None:
        _seed_installed(cache_dir, "terraform", "1.9.5")
        result = runner.invoke(
            main, args=["--conf-dir", str(tmp_path), "runtime", "ls"]
        )
        assert result.exit_code == 0
        assert "terraform" in result.stdout
        assert "1.9.5" in result.stdout

    def test_flags_version_pinned_by_manifest(
        self, runner: CliRunner, cache_dir: Path, tmp_path: Path
    ) -> None:
        _seed_installed(cache_dir, "terraform", "1.9.5")
        _write_manifest_with_engine(tmp_path, "group_a", "terraform", "1.9.5")
        result = runner.invoke(
            main, args=["--conf-dir", str(tmp_path), "runtime", "ls"]
        )
        assert result.exit_code == 0
        assert "yes" in result.stdout

    def test_filters_by_engine(
        self, runner: CliRunner, cache_dir: Path, tmp_path: Path
    ) -> None:
        _seed_installed(cache_dir, "terraform", "1.9.5")
        _seed_installed(cache_dir, "opentofu", "1.7.0", "tofu")
        result = runner.invoke(
            main,
            args=[
                "--conf-dir",
                str(tmp_path),
                "runtime",
                "ls",
                "--engine",
                "opentofu",
            ],
        )
        assert result.exit_code == 0
        assert "opentofu" in result.stdout
        assert "terraform" not in result.stdout


class TestRuntimeInstall:
    def test_downloads_and_caches(
        self,
        runner: CliRunner,
        cache_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _serve(monkeypatch, _release())
        result = runner.invoke(
            main, args=["--conf-dir", str(tmp_path), "runtime", "install", "1.9.5"]
        )
        assert result.exit_code == 0
        assert (cache_dir / "terraform" / "1.9.5" / "terraform").is_file()

    @pytest.mark.usefixtures("cache_dir")
    def test_noop_when_already_cached(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        http = _release()
        _serve(monkeypatch, http)
        args = ["--conf-dir", str(tmp_path), "runtime", "install", "1.9.5"]
        assert runner.invoke(main, args=args).exit_code == 0
        calls = len(http.calls)
        result = runner.invoke(main, args=args)
        assert result.exit_code == 0
        assert len(http.calls) == calls

    @pytest.mark.usefixtures("cache_dir")
    def test_rejects_system_version(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main, args=["--conf-dir", str(tmp_path), "runtime", "install", "system"]
        )
        assert result.exit_code != 0

    def test_checksum_mismatch_fails(
        self,
        runner: CliRunner,
        cache_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _serve(monkeypatch, _release(digest="0" * 64))
        result = runner.invoke(
            main, args=["--conf-dir", str(tmp_path), "runtime", "install", "1.9.5"]
        )
        assert result.exit_code == 1
        assert "Checksum mismatch" in result.stderr
        assert not (cache_dir / "terraform" / "1.9.5").exists()

    def test_works_without_existing_conf_dir(
        self,
        runner: CliRunner,
        cache_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`install`/`prune` are exempt from `--conf-dir` needing to exist, for CI/offline pre-fetch."""
        _serve(monkeypatch, _release())
        missing_conf_dir = tmp_path / "does-not-exist"
        result = runner.invoke(
            main,
            args=["--conf-dir", str(missing_conf_dir), "runtime", "install", "1.9.5"],
        )
        assert result.exit_code == 0
        assert (cache_dir / "terraform" / "1.9.5" / "terraform").is_file()


class TestRuntimeRm:
    def test_removes_unpinned_version(
        self, runner: CliRunner, cache_dir: Path, tmp_path: Path
    ) -> None:
        _seed_installed(cache_dir, "terraform", "1.9.5")
        result = runner.invoke(
            main, args=["--conf-dir", str(tmp_path), "runtime", "rm", "1.9.5"]
        )
        assert result.exit_code == 0
        assert not (cache_dir / "terraform" / "1.9.5").exists()

    def test_blocks_pinned_version_without_force(
        self, runner: CliRunner, cache_dir: Path, tmp_path: Path
    ) -> None:
        _seed_installed(cache_dir, "terraform", "1.9.5")
        _write_manifest_with_engine(tmp_path, "group_a", "terraform", "1.9.5")
        result = runner.invoke(
            main, args=["--conf-dir", str(tmp_path), "runtime", "rm", "1.9.5"]
        )
        assert result.exit_code == 1
        assert "still pinned" in result.stderr
        assert (cache_dir / "terraform" / "1.9.5").exists()

    def test_force_removes_pinned_version(
        self, runner: CliRunner, cache_dir: Path, tmp_path: Path
    ) -> None:
        _seed_installed(cache_dir, "terraform", "1.9.5")
        _write_manifest_with_engine(tmp_path, "group_a", "terraform", "1.9.5")
        result = runner.invoke(
            main,
            args=["--conf-dir", str(tmp_path), "runtime", "rm", "1.9.5", "--force"],
        )
        assert result.exit_code == 0
        assert not (cache_dir / "terraform" / "1.9.5").exists()

    @pytest.mark.usefixtures("cache_dir")
    def test_never_installed_version_fails(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            main, args=["--conf-dir", str(tmp_path), "runtime", "rm", "1.9.5"]
        )
        assert result.exit_code == 1
        assert "isn't installed" in result.stderr


class TestRuntimePrune:
    def test_removes_versions_not_in_use(
        self, runner: CliRunner, cache_dir: Path, tmp_path: Path
    ) -> None:
        _seed_installed(cache_dir, "terraform", "1.9.5")
        _seed_installed(cache_dir, "terraform", "1.8.0")
        _write_manifest_with_engine(tmp_path, "group_a", "terraform", "1.9.5")
        result = runner.invoke(
            main, args=["--conf-dir", str(tmp_path), "runtime", "prune"]
        )
        assert result.exit_code == 0
        assert (cache_dir / "terraform" / "1.9.5").exists()
        assert not (cache_dir / "terraform" / "1.8.0").exists()

    def test_keeps_everything_when_all_in_use(
        self, runner: CliRunner, cache_dir: Path, tmp_path: Path
    ) -> None:
        _seed_installed(cache_dir, "terraform", "1.9.5")
        _write_manifest_with_engine(tmp_path, "group_a", "terraform", "1.9.5")
        result = runner.invoke(
            main, args=["--conf-dir", str(tmp_path), "runtime", "prune"]
        )
        assert result.exit_code == 0
        assert (cache_dir / "terraform" / "1.9.5").exists()
        assert "Nothing to prune" in result.stdout

    def test_dry_run_does_not_delete(
        self, runner: CliRunner, cache_dir: Path, tmp_path: Path
    ) -> None:
        _seed_installed(cache_dir, "terraform", "1.9.5")
        result = runner.invoke(
            main, args=["--conf-dir", str(tmp_path), "runtime", "prune", "--dry-run"]
        )
        assert result.exit_code == 0
        assert (cache_dir / "terraform" / "1.9.5").exists()
