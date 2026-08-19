from click.testing import CliRunner

from terranova.cli import main
from tests import PROJECT_TESTS_FIXTURES_DIR
from tests.conftest import FakeTerraform


def test_graph_success(runner: CliRunner, fake_terraform_bin: FakeTerraform) -> None:
    _ = fake_terraform_bin
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    result = runner.invoke(
        main, args=["--conf-dir", str(fixture_dir), "graph", "main_group"]
    )
    assert result.exit_code == 0


def test_graph_failure_propagates_exit_code(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    fake_terraform_bin.set_exit_code(2)
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    result = runner.invoke(
        main, args=["--conf-dir", str(fixture_dir), "graph", "main_group"]
    )
    assert result.exit_code == 2
