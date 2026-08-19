from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Final

import click
import pytest
from click.exceptions import Exit

from terranova.binds import Terraform
from terranova.commands.helpers import (
    SelectorType,
    discover_resources,
    extract_import_vars,
    extract_output_var,
    find_all_resource_dirs,
    mount_context,
    read_manifest,
    resource_dirs,
)
from terranova.resources import (
    ResourcesImport,
    ResourcesManifest,
    ResourcesMetadata,
    Selector,
)
from terranova.utils import SharedContext
from tests.conftest import FakeTerraform

_VALID_MANIFEST: Final[str] = """
version: "1.0"
metadata:
  name: test
  description: test
"""


def _write_manifest_dir(base: Path, *parts: str) -> Path:
    resource_dir = base.joinpath(*parts)
    resource_dir.mkdir(parents=True, exist_ok=True)
    (resource_dir / "manifest.yml").write_text(textwrap.dedent(_VALID_MANIFEST))
    return resource_dir


class TestSelectorTypeConvert:
    def _convert(self, value: object) -> Selector:
        return SelectorType().convert(value, None, None)

    def test_convert_rejects_non_str(self) -> None:
        with pytest.raises(click.BadParameter):
            self._convert(123)

    def test_convert_no_equals_sign(self) -> None:
        selector = self._convert("name")
        assert selector.name == "name"
        assert selector.value is None

    def test_convert_with_equals_sign(self) -> None:
        selector = self._convert("name=value")
        assert selector.name == "name"
        assert selector.value == "value"

    def test_convert_multiple_equals_signs_splits_on_first_only(self) -> None:
        selector = self._convert("name=a=b")
        assert selector.name == "name"
        assert selector.value == "a=b"

    def test_convert_empty_string(self) -> None:
        selector = self._convert("")
        assert selector.name == ""
        assert selector.value is None


class TestReadManifest:
    def test_read_manifest_success(self, tmp_path: Path) -> None:
        resource_dir = _write_manifest_dir(tmp_path, "resources", "group_a")
        manifest = read_manifest(resource_dir)
        assert manifest.metadata.name == "test"

    def test_read_manifest_missing_calls_log_fatal(self, tmp_path: Path) -> None:
        with pytest.raises(Exit):
            read_manifest(tmp_path / "does_not_exist")

    def test_read_manifest_invalid_calls_log_fatal(self, tmp_path: Path) -> None:
        resource_dir = tmp_path / "resources" / "group_a"
        resource_dir.mkdir(parents=True)
        (resource_dir / "manifest.yml").write_text("key: [unclosed")
        with pytest.raises(Exit):
            read_manifest(resource_dir)


class TestDiscoverResources:
    def test_discover_resources_success(self, tmp_path: Path) -> None:
        resource_dir = tmp_path / "group_a"
        resource_dir.mkdir()
        (resource_dir / "main.tf").write_text(
            '/* @name foo */\nresource "aws_x" "y" {}\n'
        )
        resources = discover_resources(resource_dir)
        assert len(resources) == 1

    def test_discover_resources_invalid_calls_log_fatal(self, tmp_path: Path) -> None:
        resource_dir = tmp_path / "group_a"
        resource_dir.mkdir()
        (resource_dir / "main.tf").write_text('resource "aws_x" "y" {}\n')
        with pytest.raises(Exit):
            discover_resources(resource_dir)


class TestFindAllResourceDirs:
    def test_relative_path_matches_dir_name(self, tmp_path: Path) -> None:
        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        group_dir = _write_manifest_dir(tmp_path, "resources", "group_a")
        result = find_all_resource_dirs(SharedContext.resources_dir())
        assert result == [(group_dir, "group_a")]

    def test_nested_relative_path(self, tmp_path: Path) -> None:
        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        nested_dir = _write_manifest_dir(tmp_path, "resources", "team", "group_a")
        result = find_all_resource_dirs(SharedContext.resources_dir())
        assert result == [(nested_dir, "team/group_a")]

    def test_ignores_non_manifest_files(self, tmp_path: Path) -> None:
        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        other_dir = tmp_path / "resources" / "group_a"
        other_dir.mkdir(parents=True)
        (other_dir / "other.yml").write_text("not a manifest")
        result = find_all_resource_dirs(SharedContext.resources_dir())
        assert result == []

    def test_bug_relative_path_slicing_is_anchored_to_global_resources_dir(
        self, tmp_path: Path
    ) -> None:
        """
        Regression test documenting a landmine: `find_all_resource_dirs`
        slices relative paths using
        `len(SharedContext.resources_dir().as_posix())` (the *global*
        conf-derived path), not a length derived from its own
        `resources_dir` parameter. As long as callers only ever pass in
        `SharedContext.resources_dir()` or a descendant of it (as
        `resource_dirs()` does), the slice is correct. But calling this
        function directly with a directory that is NOT a descendant of the
        global resources dir produces a garbage relative path rather than a
        clean one — this must not be "fixed" accidentally as a side effect
        of an unrelated refactor without also updating callers.
        """
        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path / "conf")
        other_root = tmp_path / "elsewhere"
        group_dir = other_root / "group_a"
        group_dir.mkdir(parents=True)
        (group_dir / "manifest.yml").write_text(textwrap.dedent(_VALID_MANIFEST))

        result = find_all_resource_dirs(other_root)

        assert result[0][1] != "group_a"


class TestResourceDirs:
    def test_no_manifests_returns_empty_list(self, tmp_path: Path) -> None:
        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        (tmp_path / "resources").mkdir()
        assert resource_dirs(None) == []

    def test_nonexistent_path_returns_empty_list(self, tmp_path: Path) -> None:
        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        assert resource_dirs("does/not/exist") == []

    def test_with_path_filters_to_subdir(self, tmp_path: Path) -> None:
        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        group_a = _write_manifest_dir(tmp_path, "resources", "group_a")
        _write_manifest_dir(tmp_path, "resources", "group_b")
        result = resource_dirs("group_a")
        assert result == [(group_a, "group_a")]

    def test_without_path_returns_all(self, tmp_path: Path) -> None:
        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        _write_manifest_dir(tmp_path, "resources", "group_a")
        _write_manifest_dir(tmp_path, "resources", "group_b")
        result = resource_dirs(None)
        assert {rel for _, rel in result} == {"group_a", "group_b"}


class TestMountContext:
    def test_reads_manifest_when_not_provided(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        _ = fake_terraform_bin
        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        resource_dir = _write_manifest_dir(tmp_path, "resources", "group_a")
        terraform = mount_context(resource_dir)
        assert isinstance(terraform, Terraform)

    def test_uses_provided_manifest_skips_read(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        _ = fake_terraform_bin
        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        resource_dir = tmp_path / "resources" / "group_a"
        resource_dir.mkdir(parents=True)
        (resource_dir / "manifest.yml").write_text("key: [unclosed")
        manifest = ResourcesManifest(
            metadata=ResourcesMetadata(name="x", description="y")
        )
        # Does not raise despite the on-disk manifest being invalid YAML,
        # because the provided manifest short-circuits read_manifest().
        terraform = mount_context(resource_dir, manifest=manifest)
        assert isinstance(terraform, Terraform)

    def test_import_vars_true_resolves_and_forwards_variables(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        _write_manifest_dir(tmp_path, "resources", "producer")
        consumer_dir = tmp_path / "resources" / "consumer"
        consumer_dir.mkdir(parents=True)
        manifest = ResourcesManifest(
            metadata=ResourcesMetadata(name="consumer", description="d"),
            imports=[
                ResourcesImport(
                    source="producer", resource="some_output", target="input_var"
                )
            ],
        )
        fake_terraform_bin.set_stdout("chained-value")
        terraform = mount_context(consumer_dir, manifest=manifest, import_vars=True)
        terraform.graph()
        assert fake_terraform_bin.captured_env["TF_VAR_input_var"] == "chained-value"


class TestExtractImportVars:
    def test_empty_when_no_imports(self) -> None:
        manifest = ResourcesManifest(
            metadata=ResourcesMetadata(name="x", description="y")
        )
        assert extract_import_vars(manifest) == {}

    def test_uses_resource_name_when_target_absent(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        _write_manifest_dir(tmp_path, "resources", "producer")
        fake_terraform_bin.set_stdout("val")
        manifest = ResourcesManifest(
            metadata=ResourcesMetadata(name="x", description="y"),
            imports=[ResourcesImport(source="producer", resource="foo")],
        )
        assert extract_import_vars(manifest) == {"foo": "val"}

    def test_uses_target_when_present(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        _write_manifest_dir(tmp_path, "resources", "producer")
        fake_terraform_bin.set_stdout("val")
        manifest = ResourcesManifest(
            metadata=ResourcesMetadata(name="x", description="y"),
            imports=[ResourcesImport(source="producer", resource="foo", target="bar")],
        )
        assert extract_import_vars(manifest) == {"bar": "val"}

    def test_multiple_imports_all_resolved(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        _write_manifest_dir(tmp_path, "resources", "producer_a")
        _write_manifest_dir(tmp_path, "resources", "producer_b")
        fake_terraform_bin.set_stdout("val")
        manifest = ResourcesManifest(
            metadata=ResourcesMetadata(name="x", description="y"),
            imports=[
                ResourcesImport(source="producer_a", resource="foo"),
                ResourcesImport(source="producer_b", resource="bar"),
            ],
        )
        result = extract_import_vars(manifest)
        assert result == {"foo": "val", "bar": "val"}


class TestExtractOutputVar:
    def test_success(self, tmp_path: Path, fake_terraform_bin: FakeTerraform) -> None:
        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        _write_manifest_dir(tmp_path, "resources", "producer")
        fake_terraform_bin.set_stdout("value123")
        assert extract_output_var("producer", "some_name") == "value123"

    def test_error_return_code_raises_exit_with_matching_code(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        _write_manifest_dir(tmp_path, "resources", "producer")
        fake_terraform_bin.set_exit_code(5)
        with pytest.raises(Exit) as exc_info:
            extract_output_var("producer", "some_name")
        assert exc_info.value.exit_code == 5
