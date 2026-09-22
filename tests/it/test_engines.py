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
    EngineNotInstalledError,
    UnsupportedEnginePlatformError,
)
from terranova.resources import ResourcesEngine, ResourcesManifest, ResourcesMetadata

LINUX = EnginePlatform("linux", "amd64")
WINDOWS = EnginePlatform("windows", "amd64")
ENGINE_NAMES = ["terraform", "opentofu"]


def _binary_name(engine_name: str, os_name: str = "linux") -> str:
    base = engines.ENGINE_DESCRIPTORS[engine_name].binary_base_name
    return f"{base}.exe" if os_name == "windows" else base


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


def _release(
    engine_name: str = "terraform",
    target: EnginePlatform = LINUX,
    digest: str | None = None,
) -> FakeHttp:
    """Serve a fake release, with the archive's real checksum unless overridden."""
    descriptor = engines.ENGINE_DESCRIPTORS[engine_name]
    archive = _zip_bytes(_binary_name(engine_name, target.os_name))

    def handler(url: str) -> _Response:
        if url.endswith("SHA256SUMS"):
            name = url.rsplit("/", 1)[-1].replace("SHA256SUMS", "")
            version = name.removeprefix(f"{descriptor.binary_base_name}_").removesuffix(
                "_"
            )
            sha = digest or hashlib.sha256(archive).hexdigest()
            archive_name = descriptor.archive_name(version, target)
            return _Response(200, f"{sha}  {archive_name}\n".encode())
        return _Response(200, archive)

    return FakeHttp(handler)


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the manager's cache at `tmp_path` through the home directory."""
    monkeypatch.setenv("HOME", tmp_path.as_posix())
    monkeypatch.setenv("USERPROFILE", tmp_path.as_posix())
    return tmp_path / ".terranova" / "engines"


@pytest.fixture
def linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")


def _serve(monkeypatch: pytest.MonkeyPatch, http: FakeHttp) -> None:
    # A plain function on the class, so it receives the pool as `self` like the real method
    def request(_pool: urllib3.PoolManager, method: str, url: str) -> _Response:
        return http.request(method, url)

    monkeypatch.setattr(urllib3.PoolManager, "request", request)


def _manifest(version: str | None, name: str = "terraform") -> ResourcesManifest:
    return ResourcesManifest(
        metadata=ResourcesMetadata(name="n", description="d"),
        engine=ResourcesEngine(name, version) if version else None,
    )


@pytest.mark.usefixtures("cache_dir", "linux")
@pytest.mark.parametrize(
    "engine",
    [
        None,
        ResourcesEngine("terraform", "system"),
        ResourcesEngine("opentofu", "system"),
    ],
)
def test_system_and_none_use_path(
    monkeypatch: pytest.MonkeyPatch, engine: ResourcesEngine | None
) -> None:
    http = _release()
    _serve(monkeypatch, http)
    assert EngineManager().resolve(engine) is None
    assert http.calls == []


@pytest.mark.usefixtures("linux")
@pytest.mark.parametrize("engine_name", ENGINE_NAMES)
def test_download_then_cache_hit(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch, engine_name: str
) -> None:
    http = _release(engine_name)
    _serve(monkeypatch, http)
    manager = EngineManager()
    engine = ResourcesEngine(engine_name, "1.9.5")

    binary = manager.resolve(engine)
    assert binary is not None
    assert binary == cache_dir / engine_name / "1.9.5" / _binary_name(engine_name)
    assert binary.is_file() and binary.stat().st_mode & 0o100
    assert len(http.calls) == 2

    assert manager.resolve(engine) == binary
    assert len(http.calls) == 2
    assert [p.name for p in (cache_dir / engine_name).iterdir()] == ["1.9.5"]


@pytest.mark.usefixtures("linux")
@pytest.mark.parametrize("engine_name", ENGINE_NAMES)
def test_checksum_mismatch(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch, engine_name: str
) -> None:
    _serve(monkeypatch, _release(engine_name, digest="0" * 64))
    with pytest.raises(EngineChecksumError):
        EngineManager().resolve(ResourcesEngine(engine_name, "1.9.5"))
    assert not (cache_dir / engine_name / "1.9.5").exists()


@pytest.mark.usefixtures("cache_dir", "linux")
@pytest.mark.parametrize("engine_name", ENGINE_NAMES)
def test_missing_checksum_entry(
    monkeypatch: pytest.MonkeyPatch, engine_name: str
) -> None:
    archive = _zip_bytes()
    _serve(
        monkeypatch,
        FakeHttp(
            lambda url: _Response(200, b"" if url.endswith("SHA256SUMS") else archive)
        ),
    )
    with pytest.raises(EngineDownloadError):
        EngineManager().resolve(ResourcesEngine(engine_name, "1.9.5"))


@pytest.mark.usefixtures("linux")
@pytest.mark.parametrize("engine_name", ENGINE_NAMES)
def test_invalid_archive(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch, engine_name: str
) -> None:
    descriptor = engines.ENGINE_DESCRIPTORS[engine_name]
    archive = _zip_bytes("other")
    sha = hashlib.sha256(archive).hexdigest()
    archive_name = descriptor.archive_name("1.9.5", LINUX)
    _serve(
        monkeypatch,
        FakeHttp(
            lambda url: _Response(
                200,
                f"{sha}  {archive_name}\n".encode()
                if url.endswith("SHA256SUMS")
                else archive,
            )
        ),
    )
    with pytest.raises(EngineDownloadError, match="invalid archive"):
        EngineManager().resolve(ResourcesEngine(engine_name, "1.9.5"))
    assert list((cache_dir / engine_name).iterdir()) == []


@pytest.mark.usefixtures("cache_dir")
@pytest.mark.parametrize("engine_name", ENGINE_NAMES)
def test_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch, engine_name: str
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Plan9")
    with pytest.raises(UnsupportedEnginePlatformError):
        EngineManager().resolve(ResourcesEngine(engine_name, "1.9.5"))


@pytest.mark.parametrize("engine_name", ENGINE_NAMES)
def test_windows_uses_exe(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch, engine_name: str
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    _serve(monkeypatch, _release(engine_name, WINDOWS))
    binary = EngineManager().resolve(ResourcesEngine(engine_name, "1.9.5"))
    assert binary is not None
    assert binary == cache_dir / engine_name / "1.9.5" / _binary_name(
        engine_name, "windows"
    )
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
    assert sorted(p.name for p in (cache_dir / "terraform").iterdir()) == [
        "1.8.0",
        "1.9.5",
    ]
    assert len(http.calls) == 4


@pytest.mark.usefixtures("linux")
def test_prepare_dedups_by_engine_and_version(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same version string on two different engines must not collide in the cache."""
    tf = engines.ENGINE_DESCRIPTORS["terraform"]
    ot = engines.ENGINE_DESCRIPTORS["opentofu"]
    tf_archive = _zip_bytes(_binary_name("terraform"))
    ot_archive = _zip_bytes(_binary_name("opentofu"))

    def handler(url: str) -> _Response:
        descriptor, archive = (
            (ot, ot_archive) if "opentofu" in url else (tf, tf_archive)
        )
        if url.endswith("SHA256SUMS"):
            name = url.rsplit("/", 1)[-1].replace("SHA256SUMS", "")
            version = name.removeprefix(f"{descriptor.binary_base_name}_").removesuffix(
                "_"
            )
            sha = hashlib.sha256(archive).hexdigest()
            archive_name = descriptor.archive_name(version, LINUX)
            return _Response(200, f"{sha}  {archive_name}\n".encode())
        return _Response(200, archive)

    http = FakeHttp(handler)
    _serve(monkeypatch, http)
    EngineManager().prepare(
        [_manifest("1.9.5", "terraform"), _manifest("1.9.5", "opentofu")]
    )
    assert sorted(p.name for p in cache_dir.iterdir()) == ["opentofu", "terraform"]
    assert (cache_dir / "terraform" / "1.9.5" / "terraform").is_file()
    assert (cache_dir / "opentofu" / "1.9.5" / "tofu").is_file()
    assert len(http.calls) == 4


@pytest.mark.usefixtures("cache_dir", "linux")
@pytest.mark.parametrize("engine_name", ENGINE_NAMES)
def test_non_200_raises(monkeypatch: pytest.MonkeyPatch, engine_name: str) -> None:
    _serve(monkeypatch, FakeHttp(lambda _url: _Response(404)))
    with pytest.raises(EngineDownloadError, match="HTTP 404"):
        EngineManager().resolve(ResourcesEngine(engine_name, "9.9.9"))


@pytest.mark.usefixtures("cache_dir", "linux")
@pytest.mark.parametrize("engine_name", ENGINE_NAMES)
def test_transport_error_raises(
    monkeypatch: pytest.MonkeyPatch, engine_name: str
) -> None:
    def boom(_url: str) -> _Response:
        raise urllib3.exceptions.HTTPError("connection reset")

    _serve(monkeypatch, FakeHttp(boom))
    with pytest.raises(EngineDownloadError, match="connection reset"):
        EngineManager().resolve(ResourcesEngine(engine_name, "1.9.5"))


def test_default_engine_manager_is_shared() -> None:
    assert engines.default_engine_manager() is engines.default_engine_manager()


def _with_latest(
    release: FakeHttp, engine_name: str, body: bytes | Exception
) -> FakeHttp:
    """Wrap a fake release so the engine's `latest` endpoint answers with `body`."""
    inner = release.handler
    latest_url = engines.ENGINE_DESCRIPTORS[engine_name].latest_url

    def handler(url: str) -> _Response:
        if url == latest_url:
            if isinstance(body, Exception):
                raise body
            return _Response(200, body)
        return inner(url)

    release.handler = handler
    return release


def _latest_body(engine_name: str, version: str = "1.9.5") -> bytes:
    if engine_name == "opentofu":
        return json.dumps({"tag_name": f"v{version}"}).encode()
    return json.dumps({"current_version": version, "alerts": []}).encode()


@pytest.mark.usefixtures("linux")
@pytest.mark.parametrize("engine_name", ENGINE_NAMES)
def test_latest_resolves_and_downloads(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch, engine_name: str
) -> None:
    http = _with_latest(_release(engine_name), engine_name, _latest_body(engine_name))
    _serve(monkeypatch, http)
    binary = EngineManager().resolve(ResourcesEngine(engine_name, "latest"))
    assert binary == cache_dir / engine_name / "1.9.5" / _binary_name(engine_name)
    assert binary is not None and binary.is_file()
    assert http.calls[0] == engines.ENGINE_DESCRIPTORS[engine_name].latest_url


@pytest.mark.usefixtures("linux")
@pytest.mark.parametrize("engine_name", ENGINE_NAMES)
def test_latest_lookup_is_cached_within_ttl(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch, engine_name: str
) -> None:
    http = _with_latest(_release(engine_name), engine_name, _latest_body(engine_name))
    _serve(monkeypatch, http)
    manager = EngineManager()
    latest = ResourcesEngine(engine_name, "latest")
    manager.resolve(latest)
    calls = len(http.calls)
    expected = cache_dir / engine_name / "1.9.5" / _binary_name(engine_name)
    assert manager.resolve(latest) == expected
    assert len(http.calls) == calls


@pytest.mark.usefixtures("linux")
@pytest.mark.parametrize("engine_name", ENGINE_NAMES)
def test_latest_lookup_refreshes_after_ttl(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch, engine_name: str
) -> None:
    http = _with_latest(_release(engine_name), engine_name, _latest_body(engine_name))
    _serve(monkeypatch, http)
    latest = ResourcesEngine(engine_name, "latest")
    EngineManager().resolve(latest)

    cache_file = cache_dir / engine_name / engines.LATEST_CACHE_FILE
    stale = time.time() - engines.LATEST_TTL_SECONDS - 1
    cache_file.write_text(json.dumps({"version": "1.9.5", "checked_at": stale}))
    _with_latest(http, engine_name, _latest_body(engine_name, "1.10.0"))

    expected = cache_dir / engine_name / "1.10.0" / _binary_name(engine_name)
    assert EngineManager().resolve(latest) == expected


@pytest.mark.usefixtures("linux")
@pytest.mark.parametrize("engine_name", ENGINE_NAMES)
def test_latest_falls_back_to_stale_cache_when_offline(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch, engine_name: str
) -> None:
    http = _with_latest(_release(engine_name), engine_name, _latest_body(engine_name))
    _serve(monkeypatch, http)
    latest = ResourcesEngine(engine_name, "latest")
    EngineManager().resolve(latest)

    cache_file = cache_dir / engine_name / engines.LATEST_CACHE_FILE
    stale = time.time() - engines.LATEST_TTL_SECONDS - 1
    cache_file.write_text(json.dumps({"version": "1.9.5", "checked_at": stale}))
    _with_latest(http, engine_name, urllib3.exceptions.HTTPError("offline"))

    expected = cache_dir / engine_name / "1.9.5" / _binary_name(engine_name)
    assert EngineManager().resolve(latest) == expected


@pytest.mark.usefixtures("cache_dir", "linux")
@pytest.mark.parametrize("engine_name", ENGINE_NAMES)
def test_latest_fails_offline_without_cache(
    monkeypatch: pytest.MonkeyPatch, engine_name: str
) -> None:
    _serve(
        monkeypatch,
        _with_latest(
            _release(engine_name), engine_name, urllib3.exceptions.HTTPError("offline")
        ),
    )
    with pytest.raises(EngineDownloadError, match="offline"):
        EngineManager().resolve(ResourcesEngine(engine_name, "latest"))


@pytest.mark.usefixtures("cache_dir", "linux")
@pytest.mark.parametrize("engine_name", ENGINE_NAMES)
@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]"],
    ids=["not-json", "not-object"],
)
def test_latest_rejects_invalid_response(
    monkeypatch: pytest.MonkeyPatch, engine_name: str, body: bytes
) -> None:
    _serve(monkeypatch, _with_latest(_release(engine_name), engine_name, body))
    with pytest.raises(EngineDownloadError):
        EngineManager().resolve(ResourcesEngine(engine_name, "latest"))


@pytest.mark.usefixtures("cache_dir", "linux")
@pytest.mark.parametrize(
    "body",
    [b'{"current_version": "latest"}', b"{}"],
    ids=["bad-version", "missing-version"],
)
def test_latest_rejects_invalid_hashicorp_response(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    _serve(monkeypatch, _with_latest(_release("terraform"), "terraform", body))
    with pytest.raises(EngineDownloadError):
        EngineManager().resolve(ResourcesEngine("terraform", "latest"))


@pytest.mark.usefixtures("cache_dir", "linux")
@pytest.mark.parametrize(
    "body",
    [b'{"tag_name": "vlatest"}', b"{}"],
    ids=["bad-version", "missing-version"],
)
def test_latest_rejects_invalid_opentofu_response(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    _serve(monkeypatch, _with_latest(_release("opentofu"), "opentofu", body))
    with pytest.raises(EngineDownloadError):
        EngineManager().resolve(ResourcesEngine("opentofu", "latest"))


@pytest.mark.usefixtures("linux")
@pytest.mark.parametrize("engine_name", ENGINE_NAMES)
def test_latest_ignores_corrupt_cache_file(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch, engine_name: str
) -> None:
    engine_dir = cache_dir / engine_name
    engine_dir.mkdir(parents=True)
    (engine_dir / engines.LATEST_CACHE_FILE).write_text("{corrupt")
    _serve(
        monkeypatch,
        _with_latest(_release(engine_name), engine_name, _latest_body(engine_name)),
    )
    expected = engine_dir / "1.9.5" / _binary_name(engine_name)
    assert EngineManager().resolve(ResourcesEngine(engine_name, "latest")) == expected


@pytest.mark.usefixtures("linux")
def test_prepare_resolves_latest_and_exact_together(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(
        monkeypatch, _with_latest(_release(), "terraform", _latest_body("terraform"))
    )
    EngineManager().prepare(
        [_manifest("latest"), _manifest("latest"), _manifest("1.9.5")]
    )
    assert sorted(
        p.name
        for p in (cache_dir / "terraform").iterdir()
        if not p.name.startswith(".")
    ) == ["1.9.5"]


def test_pinned_engines_dedups_and_excludes_system() -> None:
    manifests = [
        _manifest("1.9.5"),
        _manifest("1.9.5"),
        _manifest("1.8.0"),
        _manifest("system"),
        _manifest(None),
    ]
    pinned = EngineManager().pinned_engines(manifests)
    assert set(pinned) == {("terraform", "1.9.5"), ("terraform", "1.8.0")}


def test_pinned_engines_empty_for_no_manifests() -> None:
    assert EngineManager().pinned_engines([]) == {}


@pytest.mark.usefixtures("cache_dir")
def test_list_installed_empty_cache() -> None:
    assert EngineManager().list_installed() == []


def test_list_installed_lists_across_engines(cache_dir: Path) -> None:
    tf_bin = cache_dir / "terraform" / "1.9.5" / "terraform"
    tf_bin.parent.mkdir(parents=True)
    tf_bin.write_bytes(b"tf-binary")
    ot_bin = cache_dir / "opentofu" / "1.7.0" / "tofu"
    ot_bin.parent.mkdir(parents=True)
    ot_bin.write_bytes(b"tofu-binary")
    # A stray `.latest.json` next to a version dir must not be listed as a version.
    (cache_dir / "terraform" / engines.LATEST_CACHE_FILE).write_text("{}")

    installed = EngineManager().list_installed()
    assert sorted((i.engine_name, i.version) for i in installed) == [
        ("opentofu", "1.7.0"),
        ("terraform", "1.9.5"),
    ]
    tf_entry = next(i for i in installed if i.engine_name == "terraform")
    assert tf_entry.size_bytes == len(b"tf-binary")
    assert tf_entry.path == tf_bin.parent


def test_list_installed_filters_by_engine(cache_dir: Path) -> None:
    for name, binary in (("terraform", "terraform"), ("opentofu", "tofu")):
        path = cache_dir / name / "1.0.0" / binary
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x")
    installed = EngineManager().list_installed("opentofu")
    assert [i.engine_name for i in installed] == ["opentofu"]


def test_remove_deletes_installed_version(cache_dir: Path) -> None:
    path = cache_dir / "terraform" / "1.9.5" / "terraform"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    EngineManager().remove("terraform", "1.9.5")
    assert not path.parent.exists()


@pytest.mark.usefixtures("cache_dir")
def test_remove_raises_when_not_installed() -> None:
    with pytest.raises(EngineNotInstalledError):
        EngineManager().remove("terraform", "1.9.5")


def test_prune_removes_versions_not_kept(cache_dir: Path) -> None:
    kept = cache_dir / "terraform" / "1.9.5" / "terraform"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"x")
    stale = cache_dir / "terraform" / "1.8.0" / "terraform"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x")

    removed = EngineManager().prune([("terraform", "1.9.5")])
    assert [(i.engine_name, i.version) for i in removed] == [("terraform", "1.8.0")]
    assert kept.parent.exists()
    assert not stale.parent.exists()


def test_prune_removes_nothing_when_all_kept(cache_dir: Path) -> None:
    kept = cache_dir / "terraform" / "1.9.5" / "terraform"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"x")
    removed = EngineManager().prune([("terraform", "1.9.5")])
    assert removed == []
    assert kept.parent.exists()


def test_prune_dry_run_does_not_delete(cache_dir: Path) -> None:
    stale = cache_dir / "terraform" / "1.8.0" / "terraform"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x")
    removed = EngineManager().prune([], dry_run=True)
    assert [(i.engine_name, i.version) for i in removed] == [("terraform", "1.8.0")]
    assert stale.parent.exists()
