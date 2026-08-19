from click.testing import CliRunner

from terranova.cli import main
from tests import PROJECT_TESTS_FIXTURES_DIR
from tests.conftest import FakeTerraform


def test_taint_success(runner: CliRunner, fake_terraform_bin: FakeTerraform) -> None:
    _ = fake_terraform_bin
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(fixture_dir),
            "taint",
            "main_group",
            "aws_instance.foo",
        ],
    )
    assert result.exit_code == 0


def test_taint_failure_propagates_exit_code(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    fake_terraform_bin.set_exit_code(2)
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(fixture_dir),
            "taint",
            "main_group",
            "aws_instance.foo",
        ],
    )
    assert result.exit_code == 2


def test_untaint_success(runner: CliRunner, fake_terraform_bin: FakeTerraform) -> None:
    _ = fake_terraform_bin
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(fixture_dir),
            "untaint",
            "main_group",
            "aws_instance.foo",
        ],
    )
    assert result.exit_code == 0


def test_untaint_failure_propagates_exit_code(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    fake_terraform_bin.set_exit_code(2)
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(fixture_dir),
            "untaint",
            "main_group",
            "aws_instance.foo",
        ],
    )
    assert result.exit_code == 2
