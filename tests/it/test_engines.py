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
import json
import platform
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest
import urllib3

from terranova import engines
from terranova.engines import EngineManager, EnginePlatform
from terranova.exceptions import (
    EngineChecksumError,
    EngineDownloadError,
    UnsupportedEnginePlatformError,
)
from terranova.resources import ResourcesEngine, ResourcesManifest, ResourcesMetadata

LINUX = EnginePlatform("linux", "amd64")
WINDOWS = EnginePlatform("windows", "amd64")


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


def _release(target: EnginePlatform = LINUX, digest: str | None = None) -> FakeHttp:
    """Serve a fake release, with the archive's real checksum unless overridden."""
    archive = _zip_bytes(target.binary_name)

    def handler(url: str) -> _Response:
        if url.endswith("SHA256SUMS"):
            name = url.rsplit("/", 1)[-1].replace("SHA256SUMS", "")
            version = name.removeprefix("terraform_").removesuffix("_")
            sha = digest or hashlib.sha256(archive).hexdigest()
            return _Response(
                200,
                f"{sha}  terraform_{version}_{target.os_name}_{target.arch}.zip\n".encode(),
            )
        return _Response(200, archive)

    return FakeHttp(handler)


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the manager's cache at `tmp_path` through the home directory."""
    monkeypatch.setenv("HOME", tmp_path.as_posix())
    monkeypatch.setenv("USERPROFILE", tmp_path.as_posix())
    return tmp_path / ".terranova" / "engines" / "terraform"


@pytest.fixture
def linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")


def _serve(monkeypatch: pytest.MonkeyPatch, http: FakeHttp) -> None:
    # A plain function on the class, so it receives the pool as `self` like the real method
    def request(_pool: urllib3.PoolManager, method: str, url: str) -> _Response:
        return http.request(method, url)

    monkeypatch.setattr(urllib3.PoolManager, "request", request)


def _manifest(version: str | None) -> ResourcesManifest:
    return ResourcesManifest(
        metadata=ResourcesMetadata(name="n", description="d"),
        engine=ResourcesEngine("terraform", version) if version else None,
    )


@pytest.mark.usefixtures("cache_dir", "linux")
@pytest.mark.parametrize("engine", [None, ResourcesEngine("terraform", "system")])
def test_system_and_none_use_path(
    monkeypatch: pytest.MonkeyPatch, engine: ResourcesEngine | None
) -> None:
    http = _release()
    _serve(monkeypatch, http)
    assert EngineManager().resolve(engine) is None
    assert http.calls == []


@pytest.mark.usefixtures("linux")
def test_download_then_cache_hit(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = _release()
    _serve(monkeypatch, http)
    manager = EngineManager()
    engine = ResourcesEngine("terraform", "1.9.5")

    binary = manager.resolve(engine)
    assert binary is not None
    assert binary == cache_dir / "1.9.5" / "terraform"
    assert binary.is_file() and binary.stat().st_mode & 0o100
    assert len(http.calls) == 2

    assert manager.resolve(engine) == binary
    assert len(http.calls) == 2
    assert [p.name for p in cache_dir.iterdir()] == ["1.9.5"]


@pytest.mark.usefixtures("linux")
def test_checksum_mismatch(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, _release(digest="0" * 64))
    with pytest.raises(EngineChecksumError):
        EngineManager().resolve(ResourcesEngine("terraform", "1.9.5"))
    assert not (cache_dir / "1.9.5").exists()


@pytest.mark.usefixtures("cache_dir", "linux")
def test_missing_checksum_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    archive = _zip_bytes()
    _serve(
        monkeypatch,
        FakeHttp(
            lambda url: _Response(200, b"" if url.endswith("SHA256SUMS") else archive)
        ),
    )
    with pytest.raises(EngineDownloadError):
        EngineManager().resolve(ResourcesEngine("terraform", "1.9.5"))


@pytest.mark.usefixtures("linux")
def test_invalid_archive(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = _zip_bytes("other")
    sha = hashlib.sha256(archive).hexdigest()
    _serve(
        monkeypatch,
        FakeHttp(
            lambda url: _Response(
                200,
                f"{sha}  terraform_1.9.5_linux_amd64.zip\n".encode()
                if url.endswith("SHA256SUMS")
                else archive,
            )
        ),
    )
    with pytest.raises(EngineDownloadError, match="invalid archive"):
        EngineManager().resolve(ResourcesEngine("terraform", "1.9.5"))
    assert list(cache_dir.iterdir()) == []


@pytest.mark.usefixtures("cache_dir")
def test_unsupported_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Plan9")
    with pytest.raises(UnsupportedEnginePlatformError):
        EngineManager().resolve(ResourcesEngine("terraform", "1.9.5"))


def test_windows_uses_exe(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    _serve(monkeypatch, _release(WINDOWS))
    binary = EngineManager().resolve(ResourcesEngine("terraform", "1.9.5"))
    assert binary is not None
    assert binary == cache_dir / "1.9.5" / "terraform.exe"
    assert binary.is_file()


def test_detect_platform_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    assert engines.detect_platform() == WINDOWS


@pytest.mark.usefixtures("linux")
def test_prepare_downloads_each_version_once(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = _release()
    _serve(monkeypatch, http)
    EngineManager().prepare(
        [
            _manifest("1.9.5"),
            _manifest("1.9.5"),
            _manifest("1.8.0"),
            _manifest("system"),
            _manifest(None),
        ]
    )
    assert sorted(p.name for p in cache_dir.iterdir()) == ["1.8.0", "1.9.5"]
    assert len(http.calls) == 4


@pytest.mark.usefixtures("cache_dir", "linux")
def test_non_200_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, FakeHttp(lambda _url: _Response(404)))
    with pytest.raises(EngineDownloadError, match="HTTP 404"):
        EngineManager().resolve(ResourcesEngine("terraform", "9.9.9"))


@pytest.mark.usefixtures("cache_dir", "linux")
def test_transport_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_url: str) -> _Response:
        raise urllib3.exceptions.HTTPError("connection reset")

    _serve(monkeypatch, FakeHttp(boom))
    with pytest.raises(EngineDownloadError, match="connection reset"):
        EngineManager().resolve(ResourcesEngine("terraform", "1.9.5"))


def test_default_engine_manager_is_shared() -> None:
    assert engines.default_engine_manager() is engines.default_engine_manager()


def _with_latest(release: FakeHttp, body: bytes | Exception) -> FakeHttp:
    """Wrap a fake release so the checkpoint API answers with `body`."""
    inner = release.handler

    def handler(url: str) -> _Response:
        if url == engines.LATEST_URL:
            if isinstance(body, Exception):
                raise body
            return _Response(200, body)
        return inner(url)

    release.handler = handler
    return release


def _latest_body(version: str = "1.9.5") -> bytes:
    return json.dumps({"current_version": version, "alerts": []}).encode()


LATEST = ResourcesEngine("terraform", "latest")


@pytest.mark.usefixtures("linux")
def test_latest_resolves_and_downloads(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = _with_latest(_release(), _latest_body("1.9.5"))
    _serve(monkeypatch, http)
    binary = EngineManager().resolve(LATEST)
    assert binary == cache_dir / "1.9.5" / "terraform"
    assert binary is not None and binary.is_file()
    assert http.calls[0] == engines.LATEST_URL


@pytest.mark.usefixtures("linux")
def test_latest_lookup_is_cached_within_ttl(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = _with_latest(_release(), _latest_body("1.9.5"))
    _serve(monkeypatch, http)
    manager = EngineManager()
    manager.resolve(LATEST)
    calls = len(http.calls)
    assert manager.resolve(LATEST) == cache_dir / "1.9.5" / "terraform"
    assert len(http.calls) == calls


@pytest.mark.usefixtures("linux")
def test_latest_lookup_refreshes_after_ttl(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = _with_latest(_release(), _latest_body("1.9.5"))
    _serve(monkeypatch, http)
    EngineManager().resolve(LATEST)

    cache_file = cache_dir / engines.LATEST_CACHE_FILE
    stale = time.time() - engines.LATEST_TTL_SECONDS - 1
    cache_file.write_text(json.dumps({"version": "1.9.5", "checked_at": stale}))
    _with_latest(http, _latest_body("1.10.0"))

    assert EngineManager().resolve(LATEST) == cache_dir / "1.10.0" / "terraform"


@pytest.mark.usefixtures("linux")
def test_latest_falls_back_to_stale_cache_when_offline(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = _with_latest(_release(), _latest_body("1.9.5"))
    _serve(monkeypatch, http)
    EngineManager().resolve(LATEST)

    cache_file = cache_dir / engines.LATEST_CACHE_FILE
    stale = time.time() - engines.LATEST_TTL_SECONDS - 1
    cache_file.write_text(json.dumps({"version": "1.9.5", "checked_at": stale}))
    _with_latest(http, urllib3.exceptions.HTTPError("offline"))

    assert EngineManager().resolve(LATEST) == cache_dir / "1.9.5" / "terraform"


@pytest.mark.usefixtures("cache_dir", "linux")
def test_latest_fails_offline_without_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(
        monkeypatch,
        _with_latest(_release(), urllib3.exceptions.HTTPError("offline")),
    )
    with pytest.raises(EngineDownloadError, match="offline"):
        EngineManager().resolve(LATEST)


@pytest.mark.usefixtures("cache_dir", "linux")
@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]", b'{"current_version": "latest"}', b"{}"],
    ids=["not-json", "not-object", "bad-version", "missing-version"],
)
def test_latest_rejects_invalid_response(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    _serve(monkeypatch, _with_latest(_release(), body))
    with pytest.raises(EngineDownloadError):
        EngineManager().resolve(LATEST)


@pytest.mark.usefixtures("linux")
def test_latest_ignores_corrupt_cache_file(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir.mkdir(parents=True)
    (cache_dir / engines.LATEST_CACHE_FILE).write_text("{corrupt")
    _serve(monkeypatch, _with_latest(_release(), _latest_body("1.9.5")))
    assert EngineManager().resolve(LATEST) == cache_dir / "1.9.5" / "terraform"


@pytest.mark.usefixtures("linux")
def test_prepare_resolves_latest_and_exact_together(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, _with_latest(_release(), _latest_body("1.9.5")))
    EngineManager().prepare(
        [_manifest("latest"), _manifest("latest"), _manifest("1.9.5")]
    )
    assert sorted(
        p.name for p in cache_dir.iterdir() if not p.name.startswith(".")
    ) == ["1.9.5"]
