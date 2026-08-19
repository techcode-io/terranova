from click.testing import CliRunner

from terranova.cli import main
from tests import PROJECT_TESTS_FIXTURES_DIR


def test_get_displays_table_with_resources(runner: CliRunner) -> None:
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "get_with_selectors"
    result = runner.invoke(main, args=["--conf-dir", str(fixture_dir), "get"])
    assert result.exit_code == 0
    assert "foo" in result.stdout
    assert "bar" in result.stdout


def test_get_with_selector_filters_rows(runner: CliRunner) -> None:
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "get_with_selectors"
    result = runner.invoke(
        main,
        args=["--conf-dir", str(fixture_dir), "get", "--selector", "team=alpha"],
    )
    assert result.exit_code == 0
    assert "foo" in result.stdout
    assert "bar" not in result.stdout


def test_get_with_unmatched_selector_shows_empty_table(runner: CliRunner) -> None:
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "get_with_selectors"
    result = runner.invoke(
        main,
        args=["--conf-dir", str(fixture_dir), "get", "--selector", "team=gamma"],
    )
    assert result.exit_code == 0
    assert "foo" not in result.stdout
    assert "bar" not in result.stdout
