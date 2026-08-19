from __future__ import annotations

from pathlib import Path

from terranova.exceptions import (
    AmbiguousRunbookError,
    ExplainedError,
    InvalidManifestError,
    MissingManifestError,
    MissingRunbookEnvError,
    MissingRunbookError,
    UnreadableManifestError,
    VersionManifestError,
)


class TestExplainedError:
    def test_str_uses_cause(self) -> None:
        err = ExplainedError("something broke")
        assert str(err) == "something broke"

    def test_resolution_defaults_to_none(self) -> None:
        err = ExplainedError("cause only")
        assert err.resolution is None

    def test_cause_and_resolution_properties(self) -> None:
        err = ExplainedError("cause", resolution="fix it")
        assert err.cause == "cause"
        assert err.resolution == "fix it"


class TestManifestErrors:
    def test_invalid_manifest_error_message(self) -> None:
        err = InvalidManifestError(Path("/tmp/manifest.yml"))
        assert err.cause == "Invalid `manifest.yml` file at `/tmp/manifest.yml`"
        assert err.resolution is not None

    def test_version_manifest_error_message(self) -> None:
        err = VersionManifestError("9.9")
        assert err.cause == "Manifest version `v9.9` isn't supported"
        assert err.resolution is not None

    def test_missing_manifest_error_message(self) -> None:
        err = MissingManifestError(Path("/tmp/manifest.yml"))
        assert err.cause == "Missing `manifest.yml` file at `/tmp/manifest.yml`"
        assert err.resolution is not None

    def test_unreadable_manifest_error_message(self) -> None:
        err = UnreadableManifestError(Path("/tmp/manifest.yml"))
        assert err.cause == "Unreadable `manifest.yml` file at `/tmp/manifest.yml`"
        assert err.resolution is not None


class TestRunbookErrors:
    def test_ambiguous_runbook_error_message(self) -> None:
        """
        Documents an existing stray-backtick bug in the message: there's an
        extra literal backtick after the closing one
        (`` `name` is ambiguous` ``). This is a regression test, not an
        endorsement — a future one-line fix to exceptions.py should update
        this assertion deliberately.
        """
        err = AmbiguousRunbookError("my_runbook")
        assert err.cause == "The runbook name `my_runbook` is ambiguous`"
        assert err.resolution is not None

    def test_missing_runbook_error_message(self) -> None:
        """Documents the same stray-backtick bug as AmbiguousRunbookError."""
        err = MissingRunbookError("my_runbook")
        assert err.cause == "The runbook `my_runbook` isn't defined`"
        assert err.resolution is not None

    def test_missing_runbook_env_error_message(self) -> None:
        err = MissingRunbookEnvError("MY_VAR")
        assert err.cause == "The environment variable `MY_VAR` isn't defined."
        assert err.resolution is not None
