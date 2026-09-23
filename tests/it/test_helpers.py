from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Final

import click
import pytest
from click.exceptions import Exit

from terranova.binds import Git, Terraform
from terranova.commands.helpers import (
    SelectorType,
    auto_scope_resource_dirs,
    discover_resources,
    engine_versions_in_use,
    extract_import_vars,
    extract_output_var,
    find_all_resource_dirs,
    mount_context,
    read_manifest,
    read_manifests_and_waves,
    resolve_resource_dirs,
    resource_dirs,
)
from terranova.exceptions import SelfImportNotReadyError
from terranova.resources import (
    ResourcesImport,
    ResourcesManifest,
    ResourcesMetadata,
    Selector,
)
from terranova.utils import AppContext
from tests.conftest import FakeTerraform

_VALID_MANIFEST: Final[str] = """
version: "1.0"
metadata:
  name: test
  description: test
"""


def _init_git_repo(path: Path) -> None:
    """Init a real git repo at `path` and commit everything currently in it."""
    git = Git(path)
    git.init()
    git.add()
    git.commit("initial", author=("test", "test@example.com"))


def _write_manifest_dir(base: Path, *parts: str) -> Path:
    resource_dir = base.joinpath(*parts)
    resource_dir.mkdir(parents=True, exist_ok=True)
    (resource_dir / "manifest.yml").write_text(textwrap.dedent(_VALID_MANIFEST))
    return resource_dir


def _write_manifest_dir_with_engine(
    base: Path, name: str, engine_name: str, version: str
) -> Path:
    resource_dir = base / name
    resource_dir.mkdir(parents=True, exist_ok=True)
    (resource_dir / "manifest.yml").write_text(f"""version: "1.4"
metadata:
  name: {name}
  description: test
engine:
  name: {engine_name}
  version: "{version}"
""")
    return resource_dir


def _write_manifest_dir_importing(base: Path, name: str, source: str) -> Path:
    resource_dir = base / name
    resource_dir.mkdir(parents=True, exist_ok=True)
    (resource_dir / "manifest.yml").write_text(f"""version: "1.2"
metadata:
  name: {name}
  description: test
imports:
  - from: {source}
    import: some_output
""")
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
    def test_relative_path_matches_dir_name(
        self, tmp_path: Path, resources_dir: Path
    ) -> None:
        group_dir = _write_manifest_dir(tmp_path, "resources", "group_a")
        result = find_all_resource_dirs(resources_dir)
        assert result == [(group_dir, "group_a")]

    def test_nested_relative_path(self, tmp_path: Path, resources_dir: Path) -> None:
        nested_dir = _write_manifest_dir(tmp_path, "resources", "team", "group_a")
        result = find_all_resource_dirs(resources_dir)
        assert result == [(nested_dir, "team/group_a")]

    def test_ignores_non_manifest_files(
        self, tmp_path: Path, resources_dir: Path
    ) -> None:
        other_dir = tmp_path / "resources" / "group_a"
        other_dir.mkdir(parents=True)
        (other_dir / "other.yml").write_text("not a manifest")
        result = find_all_resource_dirs(resources_dir)
        assert result == []

    def test_search_dir_restricts_scan_but_paths_stay_relative_to_resources_dir(
        self, tmp_path: Path, resources_dir: Path
    ) -> None:
        group_a = _write_manifest_dir(tmp_path, "resources", "team", "group_a")
        _write_manifest_dir(tmp_path, "resources", "other", "group_b")

        result = find_all_resource_dirs(resources_dir, resources_dir / "team")

        assert result == [(group_a, "team/group_a")]


class TestResourceDirs:
    def test_no_manifests_returns_empty_list(
        self, tmp_path: Path, resources_dir: Path
    ) -> None:
        (tmp_path / "resources").mkdir()
        assert resource_dirs(resources_dir, None) == []

    def test_nonexistent_path_returns_empty_list(self, resources_dir: Path) -> None:
        assert resource_dirs(resources_dir, "does/not/exist") == []

    def test_with_path_filters_to_subdir(
        self, tmp_path: Path, resources_dir: Path
    ) -> None:
        group_a = _write_manifest_dir(tmp_path, "resources", "group_a")
        _write_manifest_dir(tmp_path, "resources", "group_b")
        result = resource_dirs(resources_dir, "group_a")
        assert result == [(group_a, "group_a")]

    def test_without_path_returns_all(
        self, tmp_path: Path, resources_dir: Path
    ) -> None:
        _write_manifest_dir(tmp_path, "resources", "group_a")
        _write_manifest_dir(tmp_path, "resources", "group_b")
        result = resource_dirs(resources_dir, None)
        assert {rel for _, rel in result} == {"group_a", "group_b"}


class TestMountContext:
    def test_reads_manifest_when_not_provided(
        self,
        tmp_path: Path,
        fake_terraform_bin: FakeTerraform,
        resources_dir: Path,
        plugin_cache_dir: Path,
    ) -> None:
        _ = fake_terraform_bin
        resource_dir = _write_manifest_dir(tmp_path, "resources", "group_a")
        terraform = mount_context(resource_dir, resources_dir, plugin_cache_dir)
        assert isinstance(terraform, Terraform)

    def test_uses_provided_manifest_skips_read(
        self,
        tmp_path: Path,
        fake_terraform_bin: FakeTerraform,
        resources_dir: Path,
        plugin_cache_dir: Path,
    ) -> None:
        _ = fake_terraform_bin
        resource_dir = tmp_path / "resources" / "group_a"
        resource_dir.mkdir(parents=True)
        (resource_dir / "manifest.yml").write_text("key: [unclosed")
        manifest = ResourcesManifest(
            metadata=ResourcesMetadata(name="x", description="y")
        )
        # Does not raise despite the on-disk manifest being invalid YAML,
        # because the provided manifest short-circuits read_manifest().
        terraform = mount_context(
            resource_dir, resources_dir, plugin_cache_dir, manifest=manifest
        )
        assert isinstance(terraform, Terraform)

    def test_import_vars_true_resolves_and_forwards_variables(
        self,
        tmp_path: Path,
        fake_terraform_bin: FakeTerraform,
        resources_dir: Path,
        plugin_cache_dir: Path,
    ) -> None:
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
        terraform = mount_context(
            consumer_dir,
            resources_dir,
            plugin_cache_dir,
            manifest=manifest,
            import_vars=True,
        )
        terraform.graph()
        assert fake_terraform_bin.captured_env["TF_VAR_input_var"] == "chained-value"

    def test_self_import_is_skipped_not_resolved(
        self,
        tmp_path: Path,
        fake_terraform_bin: FakeTerraform,
        resources_dir: Path,
        plugin_cache_dir: Path,
    ) -> None:
        """
        `plan`/`apply`/etc. mount a group before (or during) its own apply, so a
        self-import can't be resolved yet - it's skipped rather than attempted,
        unlike `terranova runbook` (see `TestExtractImportVars`).
        """
        group_dir = tmp_path / "resources" / "group_a"
        group_dir.mkdir(parents=True)
        manifest = ResourcesManifest(
            metadata=ResourcesMetadata(name="group_a", description="d"),
            imports=[
                ResourcesImport(
                    source="group_a", resource="some_output", target="input_var"
                )
            ],
        )
        fake_terraform_bin.set_stdout("should-not-be-used")
        terraform = mount_context(
            group_dir,
            resources_dir,
            plugin_cache_dir,
            manifest=manifest,
            import_vars=True,
        )
        terraform.graph()
        assert "TF_VAR_input_var" not in fake_terraform_bin.captured_env


class TestExtractImportVars:
    def test_empty_when_no_imports(
        self, resources_dir: Path, plugin_cache_dir: Path
    ) -> None:
        manifest = ResourcesManifest(
            metadata=ResourcesMetadata(name="x", description="y")
        )
        assert extract_import_vars(manifest, resources_dir, plugin_cache_dir) == {}

    def test_uses_resource_name_when_target_absent(
        self,
        tmp_path: Path,
        fake_terraform_bin: FakeTerraform,
        resources_dir: Path,
        plugin_cache_dir: Path,
    ) -> None:
        _write_manifest_dir(tmp_path, "resources", "producer")
        fake_terraform_bin.set_stdout("val")
        manifest = ResourcesManifest(
            metadata=ResourcesMetadata(name="x", description="y"),
            imports=[ResourcesImport(source="producer", resource="foo")],
        )
        assert extract_import_vars(manifest, resources_dir, plugin_cache_dir) == {
            "foo": "val"
        }

    def test_uses_target_when_present(
        self,
        tmp_path: Path,
        fake_terraform_bin: FakeTerraform,
        resources_dir: Path,
        plugin_cache_dir: Path,
    ) -> None:
        _write_manifest_dir(tmp_path, "resources", "producer")
        fake_terraform_bin.set_stdout("val")
        manifest = ResourcesManifest(
            metadata=ResourcesMetadata(name="x", description="y"),
            imports=[ResourcesImport(source="producer", resource="foo", target="bar")],
        )
        assert extract_import_vars(manifest, resources_dir, plugin_cache_dir) == {
            "bar": "val"
        }

    def test_multiple_imports_all_resolved(
        self,
        tmp_path: Path,
        fake_terraform_bin: FakeTerraform,
        resources_dir: Path,
        plugin_cache_dir: Path,
    ) -> None:
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
        result = extract_import_vars(manifest, resources_dir, plugin_cache_dir)
        assert result == {"foo": "val", "bar": "val"}

    def test_self_import_skipped_by_default(
        self,
        tmp_path: Path,
        fake_terraform_bin: FakeTerraform,
        resources_dir: Path,
        plugin_cache_dir: Path,
    ) -> None:
        _write_manifest_dir(tmp_path, "resources", "group_a")
        manifest = ResourcesManifest(
            metadata=ResourcesMetadata(name="group_a", description="d"),
            imports=[ResourcesImport(source="group_a", resource="foo")],
        )
        result = extract_import_vars(
            manifest, resources_dir, plugin_cache_dir, self_rel_path="group_a"
        )
        assert result == {}
        assert not fake_terraform_bin.was_invoked

    def test_self_import_resolved_when_resolve_self_true(
        self,
        tmp_path: Path,
        fake_terraform_bin: FakeTerraform,
        resources_dir: Path,
        plugin_cache_dir: Path,
    ) -> None:
        """`terranova runbook` resolves a self-import from the group's own state."""
        _write_manifest_dir(tmp_path, "resources", "group_a")
        fake_terraform_bin.set_stdout("own-value")
        manifest = ResourcesManifest(
            metadata=ResourcesMetadata(name="group_a", description="d"),
            imports=[ResourcesImport(source="group_a", resource="foo")],
        )
        result = extract_import_vars(
            manifest,
            resources_dir,
            plugin_cache_dir,
            self_rel_path="group_a",
            resolve_self=True,
        )
        assert result == {"foo": "own-value"}

    def test_self_import_not_ready_raises_explained_error(
        self,
        tmp_path: Path,
        fake_terraform_bin: FakeTerraform,
        resources_dir: Path,
        plugin_cache_dir: Path,
    ) -> None:
        """A self-import whose output isn't there yet fails with a clear error."""
        _write_manifest_dir(tmp_path, "resources", "group_a")
        fake_terraform_bin.set_exit_code(1)
        manifest = ResourcesManifest(
            metadata=ResourcesMetadata(name="group_a", description="d"),
            imports=[ResourcesImport(source="group_a", resource="foo")],
        )
        with pytest.raises(SelfImportNotReadyError) as exc_info:
            extract_import_vars(
                manifest,
                resources_dir,
                plugin_cache_dir,
                self_rel_path="group_a",
                resolve_self=True,
            )
        assert "group_a" in exc_info.value.cause
        assert "foo" in exc_info.value.cause


class TestExtractOutputVar:
    def test_success(
        self,
        tmp_path: Path,
        fake_terraform_bin: FakeTerraform,
        resources_dir: Path,
        plugin_cache_dir: Path,
    ) -> None:
        _write_manifest_dir(tmp_path, "resources", "producer")
        fake_terraform_bin.set_stdout("value123")
        assert (
            extract_output_var("producer", "some_name", resources_dir, plugin_cache_dir)
            == "value123"
        )

    def test_error_return_code_raises_exit_with_matching_code(
        self,
        tmp_path: Path,
        fake_terraform_bin: FakeTerraform,
        resources_dir: Path,
        plugin_cache_dir: Path,
    ) -> None:
        _write_manifest_dir(tmp_path, "resources", "producer")
        fake_terraform_bin.set_exit_code(5)
        with pytest.raises(Exit) as exc_info:
            extract_output_var("producer", "some_name", resources_dir, plugin_cache_dir)
        assert exc_info.value.exit_code == 5


class TestAutoScopeResourceDirs:
    def test_not_a_git_repo_calls_log_fatal(
        self, tmp_path: Path, resources_dir: Path
    ) -> None:
        _write_manifest_dir(tmp_path, "resources", "group_a")
        with pytest.raises(Exit):
            auto_scope_resource_dirs(tmp_path, resources_dir)

    def test_picks_up_staged_unstaged_and_untracked_changes(
        self, tmp_path: Path, resources_dir: Path
    ) -> None:
        group_a = _write_manifest_dir(tmp_path, "resources", "group_a")
        group_b = _write_manifest_dir(tmp_path, "resources", "group_b")
        _write_manifest_dir(tmp_path, "resources", "group_c")
        (tmp_path / "unrelated.txt").write_text("root file")
        _init_git_repo(tmp_path)

        # Unstaged change in group_a.
        (group_a / "manifest.yml").write_text(
            textwrap.dedent(_VALID_MANIFEST) + "\n# changed\n"
        )
        # Staged change in group_b.
        (group_b / "main.tf").write_text('resource "aws_x" "y" {}\n')
        Git(tmp_path / "resources").add("group_b/main.tf")
        # Untracked change outside the resources dir - must be ignored.
        (tmp_path / "unrelated.txt").write_text("changed root file")

        result = auto_scope_resource_dirs(tmp_path, resources_dir)

        assert result == [(group_a, "group_a"), (group_b, "group_b")]

    def test_nested_changed_file_maps_to_owning_group(
        self, tmp_path: Path, resources_dir: Path
    ) -> None:
        group_a = _write_manifest_dir(tmp_path, "resources", "group_a")
        nested_dir = group_a / "modules" / "x"
        nested_dir.mkdir(parents=True)
        (nested_dir / "main.tf").write_text('resource "aws_x" "y" {}\n')
        _init_git_repo(tmp_path)

        # Untracked file nested below group_a's manifest dir.
        (nested_dir / "new.tf").write_text('resource "aws_x" "z" {}\n')

        result = auto_scope_resource_dirs(tmp_path, resources_dir)

        assert result == [(group_a, "group_a")]

    def test_no_changes_returns_empty_list(
        self, tmp_path: Path, resources_dir: Path
    ) -> None:
        _write_manifest_dir(tmp_path, "resources", "group_a")
        _init_git_repo(tmp_path)

        assert auto_scope_resource_dirs(tmp_path, resources_dir) == []


class TestResolveResourceDirs:
    def test_path_and_auto_scope_together_raises_usage_error(
        self, tmp_path: Path, resources_dir: Path
    ) -> None:
        _write_manifest_dir(tmp_path, "resources", "group_a")
        with pytest.raises(click.UsageError):
            resolve_resource_dirs(tmp_path, resources_dir, "group_a", True)

    def test_without_auto_scope_delegates_to_resource_dirs(
        self, tmp_path: Path, resources_dir: Path
    ) -> None:
        group_a = _write_manifest_dir(tmp_path, "resources", "group_a")
        _write_manifest_dir(tmp_path, "resources", "group_b")
        result = resolve_resource_dirs(tmp_path, resources_dir, "group_a", False)
        assert result == [(group_a, "group_a")]

    def test_auto_scope_delegates_to_auto_scope_resource_dirs(
        self, tmp_path: Path, resources_dir: Path
    ) -> None:
        group_a = _write_manifest_dir(tmp_path, "resources", "group_a")
        _write_manifest_dir(tmp_path, "resources", "group_b")
        _init_git_repo(tmp_path)
        (group_a / "manifest.yml").write_text(
            textwrap.dedent(_VALID_MANIFEST) + "\n# changed\n"
        )

        result = resolve_resource_dirs(tmp_path, resources_dir, None, True)

        assert result == [(group_a, "group_a")]


class TestEngineVersionsInUse:
    def test_no_manifests_returns_empty_set(self, tmp_path: Path) -> None:
        ctx = AppContext(conf_dir=tmp_path)
        assert engine_versions_in_use(ctx) == set()

    def test_missing_resources_dir_returns_empty_set(self, tmp_path: Path) -> None:
        ctx = AppContext(conf_dir=tmp_path / "does-not-exist")
        assert engine_versions_in_use(ctx) == set()

    def test_collects_pinned_versions_across_manifests(self, tmp_path: Path) -> None:
        resources_dir = tmp_path / "resources"
        _write_manifest_dir_with_engine(resources_dir, "group_a", "terraform", "1.9.5")
        _write_manifest_dir_with_engine(resources_dir, "group_b", "opentofu", "1.7.0")
        ctx = AppContext(conf_dir=tmp_path)
        assert engine_versions_in_use(ctx) == {
            ("terraform", "1.9.5"),
            ("opentofu", "1.7.0"),
        }

    def test_excludes_system_and_unpinned_manifests(self, tmp_path: Path) -> None:
        resources_dir = tmp_path / "resources"
        _write_manifest_dir_with_engine(resources_dir, "group_a", "terraform", "system")
        _write_manifest_dir(resources_dir, "group_b")
        ctx = AppContext(conf_dir=tmp_path)
        assert engine_versions_in_use(ctx) == set()


class TestReadManifestsAndWaves:
    def test_two_group_cycle_calls_log_fatal(
        self, resources_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_manifest_dir_importing(resources_dir, "group_a", "group_b")
        _write_manifest_dir_importing(resources_dir, "group_b", "group_a")
        paths = resource_dirs(resources_dir, None)

        with pytest.raises(Exit) as exc_info:
            read_manifests_and_waves(paths)

        assert exc_info.value.exit_code == 1
        err = capsys.readouterr().err
        assert "Cause:" in err
        assert "Resolution:" in err
        assert "Traceback" not in err

    def test_three_group_cycle_calls_log_fatal(
        self, resources_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_manifest_dir_importing(resources_dir, "group_a", "group_b")
        _write_manifest_dir_importing(resources_dir, "group_b", "group_c")
        _write_manifest_dir_importing(resources_dir, "group_c", "group_a")
        paths = resource_dirs(resources_dir, None)

        with pytest.raises(Exit) as exc_info:
            read_manifests_and_waves(paths)

        assert exc_info.value.exit_code == 1
        err = capsys.readouterr().err
        assert "Cause:" in err
        assert "Resolution:" in err
        assert "Traceback" not in err
