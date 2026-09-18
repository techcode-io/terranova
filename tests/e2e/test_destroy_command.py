from pathlib import Path

from click.testing import CliRunner

from terranova.cli import main
from tests import PROJECT_TESTS_FIXTURES_DIR
from tests.conftest import FakeTerraform
from tests.e2e.conftest import copy_as_git_repo


def test_destroy_success(runner: CliRunner, fake_terraform_bin: FakeTerraform) -> None:
    _ = fake_terraform_bin
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    result = runner.invoke(main, args=["--conf-dir", str(fixture_dir), "destroy"])
    assert result.exit_code == 0


def test_destroy_failure_propagates_exit_code(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    fake_terraform_bin.set_exit_code(2)
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    result = runner.invoke(main, args=["--conf-dir", str(fixture_dir), "destroy"])
    assert result.exit_code == 2


def test_destroy_auto_scope_only_processes_changed_group(
    runner: CliRunner, fake_terraform_bin: FakeTerraform, tmp_path: Path
) -> None:
    _ = fake_terraform_bin
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "plan_multi_group"
    conf_dir = tmp_path / "conf"
    copy_as_git_repo(fixture_dir, conf_dir)
    (conf_dir / "resources" / "group_a" / "manifest.yml").write_text(
        (conf_dir / "resources" / "group_a" / "manifest.yml").read_text()
        + "\n# changed\n"
    )

    result = runner.invoke(
        main, args=["--conf-dir", str(conf_dir), "destroy", "--auto-scope"]
    )

    assert result.exit_code == 0
    assert "group_a" in result.stdout
    assert "group_b" not in result.stdout


def test_destroy_auto_scope_and_path_together_is_usage_error(
    runner: CliRunner, fake_terraform_bin: FakeTerraform, tmp_path: Path
) -> None:
    _ = fake_terraform_bin
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "plan_multi_group"
    conf_dir = tmp_path / "conf"
    copy_as_git_repo(fixture_dir, conf_dir)

    result = runner.invoke(
        main, args=["--conf-dir", str(conf_dir), "destroy", "group_a", "-A"]
    )

    assert result.exit_code != 0
    assert "--auto-scope" in result.output
