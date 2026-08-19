import os
import textwrap
from pathlib import Path
from typing import Final

from click.testing import CliRunner

from terranova.cli import main
from tests import PROJECT_TESTS_FIXTURES_DIR
from tests.conftest import FakeTerraform

_MANIFEST_WITH_DEPENDENCY: Final[str] = """
version: "1.0"
metadata:
  name: Consumer
  description: Consumes a shared dependency
dependencies:
  - source: shared_x/main.tf
    target: 00-provider.tf
"""

_MANIFEST_NO_DEPENDENCY: Final[str] = """
version: "1.0"
metadata:
  name: Consumer
  description: No dependencies
"""


def _build_conf_dir(tmp_path: Path, manifest_body: str) -> tuple[Path, Path]:
    conf_dir = tmp_path / "conf"
    consumer_dir = conf_dir / "resources" / "consumer"
    consumer_dir.mkdir(parents=True)
    (consumer_dir / "manifest.yml").write_text(textwrap.dedent(manifest_body))
    return conf_dir, consumer_dir


def _write_shared_dependency(conf_dir: Path) -> None:
    shared_x = conf_dir / "shared" / "shared_x"
    shared_x.mkdir(parents=True)
    (shared_x / "main.tf").write_text("# shared provider config\n")


class TestInitSymlinks:
    def test_creates_symlinks_for_dependencies(
        self, runner: CliRunner, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        _ = fake_terraform_bin
        conf_dir, consumer_dir = _build_conf_dir(tmp_path, _MANIFEST_WITH_DEPENDENCY)
        _write_shared_dependency(conf_dir)

        result = runner.invoke(main, args=["--conf-dir", str(conf_dir), "init"])
        assert result.exit_code == 0

        link = consumer_dir / "00-provider.tf"
        assert link.is_symlink()
        assert (
            link.resolve() == (conf_dir / "shared" / "shared_x" / "main.tf").resolve()
        )

    def test_removes_stale_symlinks_before_recreating(
        self, runner: CliRunner, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        _ = fake_terraform_bin
        conf_dir, consumer_dir = _build_conf_dir(tmp_path, _MANIFEST_WITH_DEPENDENCY)
        _write_shared_dependency(conf_dir)

        stale_target = tmp_path / "stale_target.txt"
        stale_target.write_text("stale")
        stale_link = consumer_dir / "stale_link.tf"
        stale_link.symlink_to(stale_target)

        result = runner.invoke(main, args=["--conf-dir", str(conf_dir), "init"])
        assert result.exit_code == 0
        assert not stale_link.exists()

    def test_symlink_already_exists_ignored(
        self, runner: CliRunner, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        _ = fake_terraform_bin
        conf_dir, consumer_dir = _build_conf_dir(tmp_path, _MANIFEST_WITH_DEPENDENCY)
        _write_shared_dependency(conf_dir)

        # A regular (non-symlink) file already occupies the target path, so
        # the stale-symlink removal loop won't touch it, and os.symlink()
        # hits FileExistsError, which init() silently swallows.
        existing_file = consumer_dir / "00-provider.tf"
        existing_file.write_text("pre-existing regular file")

        result = runner.invoke(main, args=["--conf-dir", str(conf_dir), "init"])
        assert result.exit_code == 0
        assert not existing_file.is_symlink()
        assert existing_file.read_text() == "pre-existing regular file"


class TestInitDirCleanup:
    def test_cleans_empty_outputs_templates_runbooks_dirs(
        self, runner: CliRunner, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        _ = fake_terraform_bin
        conf_dir, consumer_dir = _build_conf_dir(tmp_path, _MANIFEST_NO_DEPENDENCY)
        for name in ["outputs", "templates", "runbooks"]:
            (consumer_dir / name).mkdir()

        result = runner.invoke(main, args=["--conf-dir", str(conf_dir), "init"])
        assert result.exit_code == 0
        for name in ["outputs", "templates", "runbooks"]:
            assert not (consumer_dir / name).exists()

    def test_does_not_clean_non_empty_dirs(
        self, runner: CliRunner, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        _ = fake_terraform_bin
        conf_dir, consumer_dir = _build_conf_dir(tmp_path, _MANIFEST_NO_DEPENDENCY)
        outputs_dir = consumer_dir / "outputs"
        outputs_dir.mkdir()
        (outputs_dir / "keep.txt").write_text("keep me")

        result = runner.invoke(main, args=["--conf-dir", str(conf_dir), "init"])
        assert result.exit_code == 0
        assert outputs_dir.exists()
        assert (outputs_dir / "keep.txt").exists()


class TestInitTerraformInvocation:
    def test_backend_config_key_uses_relative_path(
        self, runner: CliRunner, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        conf_dir, _ = _build_conf_dir(tmp_path, _MANIFEST_NO_DEPENDENCY)
        result = runner.invoke(main, args=["--conf-dir", str(conf_dir), "init"])
        assert result.exit_code == 0
        assert "-backend-config=key=consumer" in fake_terraform_bin.captured_argv

    def test_flags_forwarded(
        self, runner: CliRunner, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        conf_dir, _ = _build_conf_dir(tmp_path, _MANIFEST_NO_DEPENDENCY)
        result = runner.invoke(
            main,
            args=[
                "--conf-dir",
                str(conf_dir),
                "init",
                "--migrate-state",
                "--no-backend",
                "--reconfigure",
                "--upgrade",
            ],
        )
        assert result.exit_code == 0
        argv = fake_terraform_bin.captured_argv
        assert "-migrate-state" in argv
        assert "-backend=false" in argv
        assert "-reconfigure" in argv
        assert "-upgrade" in argv


class TestInitFailAtEnd:
    def test_without_flag_stops_after_first(
        self, runner: CliRunner, fake_terraform_bin: FakeTerraform
    ) -> None:
        fake_terraform_bin.set_exit_code(1)
        fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "plan_multi_group"
        result = runner.invoke(main, args=["--conf-dir", str(fixture_dir), "init"])
        assert result.exit_code == 1
        assert result.stdout.count("Initializing:") == 1

    def test_with_flag_continues_all_and_exits_1(
        self, runner: CliRunner, fake_terraform_bin: FakeTerraform
    ) -> None:
        fake_terraform_bin.set_exit_code(1)
        fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "plan_multi_group"
        result = runner.invoke(
            main, args=["--conf-dir", str(fixture_dir), "init", "--fail-at-end"]
        )
        assert result.exit_code == 1
        assert result.stdout.count("Initializing:") == 2


class TestInitCwdRestoration:
    def test_cwd_restored_after_run(
        self, runner: CliRunner, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        _ = fake_terraform_bin
        conf_dir, _ = _build_conf_dir(tmp_path, _MANIFEST_NO_DEPENDENCY)
        cwd_before = os.getcwd()
        result = runner.invoke(main, args=["--conf-dir", str(conf_dir), "init"])
        assert result.exit_code == 0
        assert os.getcwd() == cwd_before
