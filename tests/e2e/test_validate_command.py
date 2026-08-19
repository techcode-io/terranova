from click.testing import CliRunner

from terranova.cli import main
from tests import PROJECT_TESTS_FIXTURES_DIR
from tests.conftest import FakeTerraform


def test_validate_success_prints_succeeded(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    _ = fake_terraform_bin
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    result = runner.invoke(main, args=["--conf-dir", str(fixture_dir), "validate"])
    assert result.exit_code == 0
    assert "Succeeded to validate" in result.stdout


def test_validate_failure_without_fail_at_end_stops_after_first(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    fake_terraform_bin.set_exit_code(1)
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "plan_multi_group"
    result = runner.invoke(main, args=["--conf-dir", str(fixture_dir), "validate"])
    assert result.exit_code == 1
    assert result.stdout.count("Validating:") == 1


def test_validate_failure_with_fail_at_end_continues_all_and_exits_1(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    fake_terraform_bin.set_exit_code(1)
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "plan_multi_group"
    result = runner.invoke(
        main, args=["--conf-dir", str(fixture_dir), "validate", "--fail-at-end"]
    )
    assert result.exit_code == 1
    assert result.stdout.count("Validating:") == 2


def test_validate_resource_metadata_missing_triggers_fatal(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    _ = fake_terraform_bin
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "validate_missing_metadata"
    result = runner.invoke(main, args=["--conf-dir", str(fixture_dir), "validate"])
    assert result.exit_code == 1
    assert "discover resources" in result.stderr
