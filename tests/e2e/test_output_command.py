from click.testing import CliRunner

from terranova.cli import main
from tests import PROJECT_TESTS_FIXTURES_DIR
from tests.conftest import FakeTerraform


def test_output_prints_without_trailing_newline(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    fake_terraform_bin.set_stdout("myvalue")
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    result = runner.invoke(
        main,
        args=["--conf-dir", str(fixture_dir), "output", "main_group", "some_name"],
    )
    assert result.exit_code == 0
    assert result.stdout == "myvalue"


def test_output_error_return_code_propagates_exit_code(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    fake_terraform_bin.set_exit_code(4)
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    result = runner.invoke(
        main,
        args=["--conf-dir", str(fixture_dir), "output", "main_group", "some_name"],
    )
    assert result.exit_code == 4
