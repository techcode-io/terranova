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
"""Download, verify and cache the terraform/opentofu binary pinned by a manifest."""

import hashlib
import io
import os
import platform
import re
import shutil
import tempfile
import time
import zipfile
from abc import ABC, abstractmethod
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Final, NamedTuple, override

import certifi
import urllib3
from serde import SerdeError, serde
from serde.json import from_json, to_json
from urllib3.util import Retry, Timeout

from terranova.exceptions import (
    EngineChecksumError,
    EngineDownloadError,
    UnsupportedEnginePlatformError,
)
from terranova.resources import ResourcesEngine, ResourcesManifest
from terranova.utils import log

HASHICORP_RELEASES_URL: Final[str] = "https://releases.hashicorp.com/terraform"
HASHICORP_LATEST_URL: Final[str] = (
    "https://checkpoint-api.hashicorp.com/v1/check/terraform"
)
OPENTOFU_RELEASES_URL: Final[str] = (
    "https://github.com/opentofu/opentofu/releases/download"
)
OPENTOFU_LATEST_URL: Final[str] = (
    "https://api.github.com/repos/opentofu/opentofu/releases/latest"
)
SYSTEM_VERSION: Final[str] = "system"
LATEST_VERSION: Final[str] = "latest"
LATEST_CACHE_FILE: Final[str] = ".latest.json"
LATEST_TTL_SECONDS: Final[int] = 24 * 60 * 60
_EXACT_VERSION: Final[re.Pattern[str]] = re.compile(r"^\d+\.\d+\.\d+$")
MAX_CONCURRENT_DOWNLOADS: Final[int] = 8
EXECUTABLE_MODE: Final[int] = 0o755

_OS_NAMES: Final[MappingProxyType[str, str]] = MappingProxyType(
    {"Linux": "linux", "Darwin": "darwin", "Windows": "windows"}
)
_ARCH_NAMES: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
)


@serde
@dataclass(frozen=True)
class LatestRelease:
    """The latest stable version and when it was looked up."""

    version: str
    checked_at: float


class EnginePlatform(NamedTuple):
    """OS and architecture names as used by official release archives."""

    os_name: str
    arch: str


def detect_platform() -> EnginePlatform:
    """
    Returns the platform used by official release archive names.

    Raises:
        UnsupportedEnginePlatformError: if there is no official release for it.
    """
    system, machine = platform.system(), platform.machine().lower()
    os_name, arch = _OS_NAMES.get(system), _ARCH_NAMES.get(machine)
    if os_name is None or arch is None:
        raise UnsupportedEnginePlatformError(system, machine)
    return EnginePlatform(os_name, arch)


def _binary_name(base_name: str, os_name: str) -> str:
    """Name of the engine executable inside the archive, or on `PATH`."""
    return f"{base_name}.exe" if os_name == "windows" else base_name


def _expected_checksum(
    engine_name: str, sums: bytes, archive_name: str, version: str
) -> str:
    """Find the checksum of `archive_name` in a `SHA256SUMS` file."""
    for line in sums.decode().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == archive_name:
            return parts[0]
    raise EngineDownloadError(
        engine_name, version, f"no checksum published for `{archive_name}`"
    )


@serde
@dataclass(frozen=True)
class _HashicorpLatestResponse:
    """Shape of HashiCorp's checkpoint API response, ignoring fields we don't need."""

    current_version: str


@serde
@dataclass(frozen=True)
class _OpenTofuLatestResponse:
    """Shape of GitHub's `releases/latest` response, ignoring fields we don't need."""

    tag_name: str


class EngineDescriptor(ABC):
    """Static, per-engine-name knowledge needed to locate and name its releases."""

    @property
    @abstractmethod
    def binary_base_name(self) -> str:
        """Name of the executable inside the archive, or on `PATH`."""

    @property
    @abstractmethod
    def latest_url(self) -> str:
        """URL to resolve the current stable version from."""

    @abstractmethod
    def archive_name(self, version: str, target: EnginePlatform) -> str:
        """Name of the release archive for `version`/`target`."""

    @abstractmethod
    def checksum_name(self, version: str) -> str:
        """Name of the published checksums file for `version`."""

    @abstractmethod
    def download_base_url(self, version: str) -> str:
        """Directory URL `archive_name`/`checksum_name` are downloaded from."""

    @abstractmethod
    def parse_latest(self, body: bytes) -> str | None:
        """Extract the current stable version from a `latest_url` response, or `None`."""


class _TerraformDescriptor(EngineDescriptor):
    """Release layout of HashiCorp's official terraform builds."""

    @property
    @override
    def binary_base_name(self) -> str:
        return "terraform"

    @property
    @override
    def latest_url(self) -> str:
        return HASHICORP_LATEST_URL

    @override
    def archive_name(self, version: str, target: EnginePlatform) -> str:
        return f"terraform_{version}_{target.os_name}_{target.arch}.zip"

    @override
    def checksum_name(self, version: str) -> str:
        return f"terraform_{version}_SHA256SUMS"

    @override
    def download_base_url(self, version: str) -> str:
        return f"{HASHICORP_RELEASES_URL}/{version}"

    @override
    def parse_latest(self, body: bytes) -> str | None:
        try:
            return from_json(_HashicorpLatestResponse, body).current_version
        except (ValueError, SerdeError):
            return None


class _OpenTofuDescriptor(EngineDescriptor):
    """Release layout of OpenTofu's GitHub releases."""

    @property
    @override
    def binary_base_name(self) -> str:
        return "tofu"

    @property
    @override
    def latest_url(self) -> str:
        return OPENTOFU_LATEST_URL

    @override
    def archive_name(self, version: str, target: EnginePlatform) -> str:
        return f"tofu_{version}_{target.os_name}_{target.arch}.zip"

    @override
    def checksum_name(self, version: str) -> str:
        return f"tofu_{version}_SHA256SUMS"

    @override
    def download_base_url(self, version: str) -> str:
        return f"{OPENTOFU_RELEASES_URL}/v{version}"

    @override
    def parse_latest(self, body: bytes) -> str | None:
        try:
            tag = from_json(_OpenTofuLatestResponse, body).tag_name
        except (ValueError, SerdeError):
            return None
        return tag.removeprefix("v")


ENGINE_DESCRIPTORS: Final[MappingProxyType[str, EngineDescriptor]] = MappingProxyType(
    {
        "terraform": _TerraformDescriptor(),
        "opentofu": _OpenTofuDescriptor(),
    }
)


class EngineManager:
    """
    Resolves pinned terraform binaries, downloading them into a shared cache.

    Thread-safe: the HTTP client is shared and each version installs into its
    own directory through an atomic rename.
    """

    def __init__(self) -> None:
        """Init engine manager."""
        # `maxsize` lets each concurrent version keep its own connection.
        self.__http = urllib3.PoolManager(
            maxsize=MAX_CONCURRENT_DOWNLOADS,
            ca_certs=certifi.where(),
            timeout=Timeout(connect=10, read=60),
            retries=Retry(
                total=3, backoff_factor=0.5, status_forcelist=(500, 502, 503, 504)
            ),
        )

    def engines_dir(self, engine_name: str) -> Path:
        """Cache directory of `engine_name` binaries, resolved lazily like `target`."""
        return Path.home() / ".terranova" / "engines" / engine_name

    @property
    def target(self) -> EnginePlatform:
        """Platform to download binaries for, resolved lazily so an unsupported one only fails on use."""
        return detect_platform()

    def resolve(self, engine: ResourcesEngine | None) -> Path | None:
        """
        Resolve the pinned binary for an engine.

        `latest` is looked up at most once per `LATEST_TTL_SECONDS`, so runs stay
        consistent and work offline once a version is installed.

        Returns:
            the cached binary path for an exact or `latest` version, downloading
            it when missing, or `None` when the system `PATH` lookup must be used.
        Raises:
            EngineError: if the platform is unsupported or the lookup, download
                or checksum verification fails.
        """
        if engine is None or engine.version == SYSTEM_VERSION:
            return None

        descriptor = ENGINE_DESCRIPTORS[engine.name]
        target = self.target
        version = (
            self.__latest_version(engine.name, descriptor)
            if engine.version == LATEST_VERSION
            else engine.version
        )
        binary = (
            self.engines_dir(engine.name)
            / version
            / _binary_name(descriptor.binary_base_name, target.os_name)
        )
        if binary.is_file():
            return binary

        archive = self.__download_verified(engine.name, descriptor, version, target)
        return self.__install(engine.name, descriptor, archive, version, target)

    def prepare(self, manifests: Iterable[ResourcesManifest]) -> None:
        """
        Ensure every distinct `latest` or exact engine version is cached.

        Distinct versions are downloaded concurrently and each one only once, so
        the parallel executor later finds them all cached.

        Raises:
            EngineError: on the first failing version.
        """
        engines = {
            (m.engine.name, m.engine.version): m.engine
            for m in manifests
            if m.engine and m.engine.version != SYSTEM_VERSION
        }
        if not engines:
            return
        workers = min(len(engines), MAX_CONCURRENT_DOWNLOADS)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self.resolve, e) for e in engines.values()]
            for future in futures:
                future.result()

    def __latest_version(self, engine_name: str, descriptor: EngineDescriptor) -> str:
        """Latest stable version, from the local lookup cache while it is fresh."""
        cache_file = self.engines_dir(engine_name) / LATEST_CACHE_FILE
        cached = self.__read_latest(cache_file)
        if cached and time.time() - cached.checked_at < LATEST_TTL_SECONDS:
            return cached.version
        try:
            version = self.__fetch_latest(engine_name, descriptor)
        except EngineDownloadError:
            # A stale answer beats failing when offline.
            if cached:
                return cached.version
            raise
        self.__write_latest(cache_file, LatestRelease(version, time.time()))
        return version

    def __fetch_latest(self, engine_name: str, descriptor: EngineDescriptor) -> str:
        """Ask the engine's release index for the current stable version."""
        log.action(f"Resolve latest {engine_name} version")
        body = self.__fetch(engine_name, descriptor.latest_url, LATEST_VERSION)
        version = descriptor.parse_latest(body)
        if version is None or not _EXACT_VERSION.match(version):
            raise EngineDownloadError(
                engine_name,
                LATEST_VERSION,
                f"unexpected version `{version}` in response",
            )
        return version

    @staticmethod
    def __read_latest(cache_file: Path) -> LatestRelease | None:
        """Read the lookup cache, treating a missing or corrupt file as empty."""
        try:
            return from_json(LatestRelease, cache_file.read_text())
        except (OSError, ValueError, SerdeError):
            return None

    def __write_latest(self, cache_file: Path, release: LatestRelease) -> None:
        """Best-effort atomic write of the lookup cache; the lookup itself already succeeded."""
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", dir=cache_file.parent, delete=False
            ) as tmp:
                tmp.write(to_json(release))
            os.replace(tmp.name, cache_file)
        except OSError:
            pass

    def __fetch(self, engine_name: str, url: str, version: str) -> bytes:
        """Download `url` in memory."""
        try:
            response = self.__http.request("GET", url)
        except urllib3.exceptions.HTTPError as err:
            raise EngineDownloadError(engine_name, version, str(err)) from err
        if response.status != 200:
            raise EngineDownloadError(
                engine_name, version, f"HTTP {response.status} for {url}"
            )
        return response.data

    def __download_verified(
        self,
        engine_name: str,
        descriptor: EngineDescriptor,
        version: str,
        target: EnginePlatform,
    ) -> bytes:
        """Download the release archive and check it against the published SHA-256."""
        archive_name = descriptor.archive_name(version, target)
        base_url = descriptor.download_base_url(version)

        log.action(f"Download {engine_name} {version}")
        archive = self.__fetch(engine_name, f"{base_url}/{archive_name}", version)
        sums = self.__fetch(
            engine_name, f"{base_url}/{descriptor.checksum_name(version)}", version
        )
        if hashlib.sha256(archive).hexdigest() != _expected_checksum(
            engine_name, sums, archive_name, version
        ):
            raise EngineChecksumError(engine_name, version)
        return archive

    def __install(
        self,
        engine_name: str,
        descriptor: EngineDescriptor,
        archive: bytes,
        version: str,
        target: EnginePlatform,
    ) -> Path:
        """Extract the binary into `engines_dir/version`, atomically, and return its path."""
        root = self.engines_dir(engine_name)
        binary_name = _binary_name(descriptor.binary_base_name, target.os_name)
        target_dir = root / version
        binary = target_dir / binary_name

        # Stage next to the destination so the final rename is atomic, which keeps
        # concurrent terranova processes sharing the cache from seeing a partial binary.
        root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{version}-", dir=root))
        try:
            try:
                with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                    (staging / binary_name).write_bytes(zf.read(binary_name))
            except (zipfile.BadZipFile, KeyError) as err:
                raise EngineDownloadError(
                    engine_name, version, f"invalid archive: {err}"
                ) from err
            (staging / binary_name).chmod(EXECUTABLE_MODE)
            try:
                os.rename(staging, target_dir)
            except OSError:
                # Another process won the race with identical, verified content.
                if not binary.is_file():
                    raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return binary


@cache
def default_engine_manager() -> EngineManager:
    """Returns the process-wide manager, so every resource group shares one HTTP pool."""
    return EngineManager()
