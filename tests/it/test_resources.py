from __future__ import annotations

import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from terranova.exceptions import (
    InvalidManifestError,
    InvalidResourcesError,
    MissingManifestError,
    MissingRunbookEnvError,
    UnreadableManifestError,
    VersionManifestError,
)
from terranova.resources import (
    Resource,
    ResourceBlockType,
    ResourcesFinder,
    ResourcesManifest,
    ResourcesRunbook,
    ResourcesRunbookEnv,
    Selector,
)


def _write_manifest(path: Path, content: str) -> Path:
    manifest = path / "manifest.yml"
    manifest.write_text(textwrap.dedent(content))
    return manifest


def _write_entrypoint(path: Path, body: str) -> str:
    script = path / "entrypoint.sh"
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script.absolute())


class TestResourcesManifestFromFile:
    def test_missing_manifest_raises(self, tmp_path: Path) -> None:
        with pytest.raises(MissingManifestError):
            ResourcesManifest.from_file(tmp_path / "manifest.yml")

    def test_unreadable_manifest_raises(self, tmp_path: Path) -> None:
        if os.geteuid() == 0:
            pytest.skip("root ignores file permissions")
        manifest = _write_manifest(
            tmp_path,
            """
            version: "1.0"
            metadata:
              name: test
              description: test
            """,
        )
        manifest.chmod(0o000)
        try:
            with pytest.raises(UnreadableManifestError):
                ResourcesManifest.from_file(manifest)
        finally:
            manifest.chmod(0o644)

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        manifest = _write_manifest(tmp_path, "key: [unclosed")
        with pytest.raises(InvalidManifestError) as exc_info:
            ResourcesManifest.from_file(manifest)
        assert exc_info.value.__cause__ is not None

    def test_missing_version_falls_back_to_v1_0_schema_but_schema_requires_version(
        self, tmp_path: Path
    ) -> None:
        """
        The `version` key defaults to "1.0" only for *schema selection*
        (`data.get("version", "1.0")`), before validation runs. Every schema
        (including v1.0) requires `version` as a top-level field, so omitting
        it entirely still fails schema validation rather than silently
        succeeding — this documents that current (surprising) behavior.
        """
        manifest = _write_manifest(
            tmp_path,
            """
            metadata:
              name: test
              description: test
            """,
        )
        with pytest.raises(InvalidManifestError):
            ResourcesManifest.from_file(manifest)

    def test_unsupported_version_raises(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path,
            """
            version: "9.9"
            metadata:
              name: test
              description: test
            """,
        )
        with pytest.raises(VersionManifestError) as exc_info:
            ResourcesManifest.from_file(manifest)
        assert "v9.9" in exc_info.value.cause

    def test_schema_validation_failure_raises_invalid_manifest(
        self, tmp_path: Path
    ) -> None:
        manifest = _write_manifest(
            tmp_path,
            """
            version: "1.0"
            """,
        )
        with pytest.raises(InvalidManifestError) as exc_info:
            ResourcesManifest.from_file(manifest)
        assert exc_info.value.__cause__ is not None

    def test_valid_v1_0_manifest_parses(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path,
            """
            version: "1.0"
            metadata:
              name: test
              description: test
            dependencies:
              - source: providers/github.tf
                target: 00-github-provider.tf
            """,
        )
        result = ResourcesManifest.from_file(manifest)
        assert result.dependencies is not None
        assert result.dependencies[0].source == "providers/github.tf"
        assert result.runbooks is None
        assert result.imports is None

    def test_valid_v1_1_manifest_with_runbooks_parses(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path,
            """
            version: "1.1"
            metadata:
              name: test
              description: test
            runbooks:
              - name: my_runbook
                entrypoint: sh
            """,
        )
        result = ResourcesManifest.from_file(manifest)
        assert result.runbooks is not None
        assert result.runbooks[0].name == "my_runbook"
        assert result.imports is None

    def test_valid_v1_2_manifest_with_imports_parses(self, tmp_path: Path) -> None:
        manifest = _write_manifest(
            tmp_path,
            """
            version: "1.2"
            metadata:
              name: test
              description: test
            imports:
              - from: ../other_group
                import: output_var
                as: input_var
            """,
        )
        result = ResourcesManifest.from_file(manifest)
        assert result.imports is not None
        assert result.imports[0].source == "../other_group"
        assert result.imports[0].resource == "output_var"
        assert result.imports[0].target == "input_var"

    def test_valid_v1_3_manifest_with_runbook_env_if_parses(
        self, tmp_path: Path
    ) -> None:
        manifest = _write_manifest(
            tmp_path,
            """
            version: "1.3"
            metadata:
              name: test
              description: test
            runbooks:
              - name: my_runbook
                entrypoint: sh
                env:
                  - name: OPTIONAL_VAR
                    if: is_defined
            """,
        )
        result = ResourcesManifest.from_file(manifest)
        assert result.runbooks is not None
        env = result.runbooks[0].env
        assert env is not None
        assert env[0].with_if == "is_defined"


class TestResourcesRunbookExec:
    """
    ResourcesRunbook.exec() always calls Command.inherit(), which binds the
    child process's stdout/stderr to sys.stdout/sys.stderr — so pytest's
    `capsys` fixture captures entrypoint output directly, without needing to
    intercept or reimplement any of the exec() internals.
    """

    def test_env_literal_value_takes_precedence(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("X", "from_env")
        entrypoint = _write_entrypoint(tmp_path, 'echo "X=$X"')
        runbook = ResourcesRunbook(
            name="rb",
            entrypoint=entrypoint,
            env=[ResourcesRunbookEnv(name="X", value="literal")],
        )
        runbook.exec("path", tmp_path, {"X": "from_import"})
        assert "X=literal" in capsys.readouterr().out

    def test_env_resolved_from_import_vars_over_os_environ(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("X", "env_val")
        entrypoint = _write_entrypoint(tmp_path, 'echo "X=$X"')
        runbook = ResourcesRunbook(
            name="rb", entrypoint=entrypoint, env=[ResourcesRunbookEnv(name="X")]
        )
        runbook.exec("path", tmp_path, {"X": "import_val"})
        assert "X=import_val" in capsys.readouterr().out

    def test_env_resolved_from_os_environ_when_not_in_import_vars(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("X", "env_val")
        entrypoint = _write_entrypoint(tmp_path, 'echo "X=$X"')
        runbook = ResourcesRunbook(
            name="rb", entrypoint=entrypoint, env=[ResourcesRunbookEnv(name="X")]
        )
        runbook.exec("path", tmp_path, {})
        assert "X=env_val" in capsys.readouterr().out

    def test_missing_env_without_is_defined_raises(self, tmp_path: Path) -> None:
        entrypoint = _write_entrypoint(tmp_path, "true")
        runbook = ResourcesRunbook(
            name="rb",
            entrypoint=entrypoint,
            env=[ResourcesRunbookEnv(name="MISSING")],
        )
        with pytest.raises(MissingRunbookEnvError):
            runbook.exec("path", tmp_path, {})

    def test_missing_env_with_wrong_if_value_raises(self, tmp_path: Path) -> None:
        entrypoint = _write_entrypoint(tmp_path, "true")
        runbook = ResourcesRunbook(
            name="rb",
            entrypoint=entrypoint,
            env=[ResourcesRunbookEnv(name="MISSING", with_if="something_else")],
        )
        with pytest.raises(MissingRunbookEnvError):
            runbook.exec("path", tmp_path, {})

    def test_missing_env_with_is_defined_is_skipped(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        entrypoint = _write_entrypoint(tmp_path, 'echo "MISSING=[$MISSING]"')
        runbook = ResourcesRunbook(
            name="rb",
            entrypoint=entrypoint,
            env=[ResourcesRunbookEnv(name="MISSING", with_if="is_defined")],
        )
        runbook.exec("path", tmp_path, {})
        assert "MISSING=[]" in capsys.readouterr().out

    def test_terranova_env_vars_always_injected(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        entrypoint = _write_entrypoint(
            tmp_path,
            'echo "PATH_VAR=$TERRANOVA_PATH"\necho "RUNBOOK_NAME=$TERRANOVA_RUNBOOK_NAME"',
        )
        runbook = ResourcesRunbook(name="my_runbook", entrypoint=entrypoint)
        runbook.exec("the_path", tmp_path, {})
        out = capsys.readouterr().out
        assert "PATH_VAR=the_path" in out
        assert "RUNBOOK_NAME=my_runbook" in out

    def test_workdir_joins_relative_to_runbooks_dir(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        entrypoint = _write_entrypoint(tmp_path, "pwd")
        runbook = ResourcesRunbook(name="rb", entrypoint=entrypoint, workdir="subdir")
        runbook.exec("path", tmp_path, {})
        assert str(subdir) in capsys.readouterr().out

    def test_args_passed_to_entrypoint(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        entrypoint = _write_entrypoint(tmp_path, 'echo "ARGS=$@"')
        runbook = ResourcesRunbook(
            name="rb", entrypoint=entrypoint, args=["foo", "bar"]
        )
        runbook.exec("path", tmp_path, {})
        assert "ARGS=foo bar" in capsys.readouterr().out

    def test_path_not_forwarded_when_unset(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # A shell entrypoint would fall back to its own built-in default PATH
        # when the env var is unset, masking the assertion — use a python
        # script that inspects os.environ directly instead.
        script = tmp_path / "print_path.py"
        script.write_text(
            "import os\nprint('PATH_IS=' + repr(os.environ.get('PATH')))\n"
        )
        monkeypatch.delenv("PATH", raising=False)
        runbook = ResourcesRunbook(
            name="rb", entrypoint=sys.executable, args=[str(script)]
        )
        runbook.exec("path", tmp_path, {})
        assert "PATH_IS=None" in capsys.readouterr().out


class TestSelectorMatch:
    def _resource(self, attrs: dict[str, list[str]]) -> Resource:
        return Resource(
            block_type=ResourceBlockType.RESOURCE,
            name="name",
            type="aws_x",
            attrs=attrs,
        )

    def test_match_by_existence_when_no_value(self) -> None:
        selector = Selector(name="attr")
        assert selector.match(self._resource({"attr": ["x"]})) is True

    def test_no_match_when_attr_absent(self) -> None:
        selector = Selector(name="attr")
        assert selector.match(self._resource({})) is False

    def test_match_by_value_in_list(self) -> None:
        selector = Selector(name="attr", value="b")
        assert selector.match(self._resource({"attr": ["a", "b"]})) is True

    def test_no_match_when_value_not_in_list(self) -> None:
        selector = Selector(name="attr", value="z")
        assert selector.match(self._resource({"attr": ["a", "b"]})) is False

    def test_match_ignores_value_when_attr_value_falsy(self) -> None:
        selector = Selector(name="attr", value="z")
        assert selector.match(self._resource({"attr": []})) is False


class TestResourcesFinder:
    def test_find_in_file_parses_resource_with_metadata(self, tmp_path: Path) -> None:
        tf_file = tmp_path / "main.tf"
        tf_file.write_text('/* @name foo\n@team bar */\nresource "aws_x" "y" {}\n')
        resources = ResourcesFinder.find_in_file(tf_file)
        assert len(resources) == 1
        resource = resources[0]
        assert resource.block_type == ResourceBlockType.RESOURCE
        assert resource.name == "y"
        assert resource.type == "aws_x"
        assert resource.attrs == {"name": ["foo"], "team": ["bar"]}

    def test_find_in_file_repeated_attr_collected_as_list(self, tmp_path: Path) -> None:
        tf_file = tmp_path / "main.tf"
        tf_file.write_text('/* @name foo\n@tag a\n@tag b */\nresource "aws_x" "y" {}\n')
        resources = ResourcesFinder.find_in_file(tf_file)
        assert resources[0].attrs["tag"] == ["a", "b"]

    def test_resource_without_metadata_raises(self, tmp_path: Path) -> None:
        tf_file = tmp_path / "main.tf"
        tf_file.write_text('resource "aws_x" "y" {}\n')
        with pytest.raises(InvalidResourcesError) as exc_info:
            ResourcesFinder.find_in_file(tf_file)
        assert "aws_x" in exc_info.value.cause
        assert "y" in exc_info.value.cause

    def test_data_block_without_metadata_does_not_raise(self, tmp_path: Path) -> None:
        tf_file = tmp_path / "main.tf"
        tf_file.write_text('data "aws_x" "y" {}\n')
        resources = ResourcesFinder.find_in_file(tf_file)
        assert len(resources) == 1
        assert resources[0].attrs == {}

    def test_find_in_file_with_selector_filters(self, tmp_path: Path) -> None:
        tf_file = tmp_path / "main.tf"
        tf_file.write_text(
            '/* @team a */\nresource "aws_x" "one" {}\n/* @team b */\nresource "aws_x" "two" {}\n'
        )
        resources = ResourcesFinder.find_in_file(
            tf_file, selectors=[Selector(name="team", value="a")]
        )
        assert len(resources) == 1
        assert resources[0].name == "one"

    def test_find_in_file_multiple_selectors_and_logic(self, tmp_path: Path) -> None:
        tf_file = tmp_path / "main.tf"
        tf_file.write_text(
            '/* @team a\n@tier gold */\nresource "aws_x" "one" {}\n/* @team a\n@tier silver */\nresource "aws_x" "two" {}\n'
        )
        resources = ResourcesFinder.find_in_file(
            tf_file,
            selectors=[
                Selector(name="team", value="a"),
                Selector(name="tier", value="gold"),
            ],
        )
        assert len(resources) == 1
        assert resources[0].name == "one"

    def test_find_in_dir_aggregates_and_sorts_by_filename(self, tmp_path: Path) -> None:
        (tmp_path / "b.tf").write_text('/* @name b */\nresource "aws_x" "b" {}\n')
        (tmp_path / "a.tf").write_text('/* @name a */\nresource "aws_x" "a" {}\n')
        resources = ResourcesFinder.find_in_dir(tmp_path)
        assert [r.name for r in resources] == ["a", "b"]

    def test_find_in_dir_ignores_non_tf_files(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text('resource "aws_x" "y" {}\n')
        resources = ResourcesFinder.find_in_dir(tmp_path)
        assert resources == []

    def test_find_in_file_no_resources_returns_empty_list(self, tmp_path: Path) -> None:
        tf_file = tmp_path / "empty.tf"
        tf_file.write_text("")
        assert ResourcesFinder.find_in_file(tf_file) == []
