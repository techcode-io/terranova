from pathlib import Path

from click.testing import CliRunner

from terranova.cli import main
from tests import PROJECT_TESTS_FIXTURES_DIR


def test_ls_lists_all_resource_dirs(runner: CliRunner) -> None:
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "plan_multi_group"
    result = runner.invoke(main, args=["--conf-dir", str(fixture_dir), "ls"])
    assert result.exit_code == 0
    assert "group_a" in result.stdout
    assert "group_b" in result.stdout


def test_ls_with_path_filters_to_subdir(runner: CliRunner) -> None:
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "plan_multi_group"
    result = runner.invoke(main, args=["--conf-dir", str(fixture_dir), "ls", "group_a"])
    assert result.exit_code == 0
    assert "group_a" in result.stdout
    assert "group_b" not in result.stdout


def test_ls_empty_resources_dir_prints_nothing(
    runner: CliRunner, tmp_path: Path
) -> None:
    (tmp_path / "resources").mkdir()
    result = runner.invoke(main, args=["--conf-dir", str(tmp_path), "ls"])
    assert result.exit_code == 0
    assert result.stdout.strip() == ""
