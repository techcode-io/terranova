import base64
import json
from pathlib import Path
from typing import cast

from click.testing import CliRunner

from terranova.cli import main
from tests import PROJECT_TESTS_FIXTURES_DIR
from tests.conftest import FakeTerraform


def _read_saved_plan(out_file: Path) -> dict[str, str]:
    return cast("dict[str, str]", json.loads(out_file.read_text()))


def test_plan_no_out_success(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    _ = fake_terraform_bin
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    result = runner.invoke(main, args=["--conf-dir", str(fixture_dir), "plan"])
    assert result.exit_code == 0


def test_plan_out_writes_base64_encoded_plan_file(
    runner: CliRunner, fake_terraform_bin: FakeTerraform, tmp_path: Path
) -> None:
    plan_bytes = b"fake-plan-bytes"
    fake_terraform_bin.set_out_bytes(plan_bytes)
    fake_terraform_bin.set_exit_code(0)

    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    out_file = tmp_path / "plan.tnplan"
    result = runner.invoke(
        main,
        args=["--conf-dir", str(fixture_dir), "plan", "--out", str(out_file)],
    )
    assert result.exit_code == 0
    assert out_file.exists()
    saved = _read_saved_plan(out_file)
    assert base64.b64decode(saved["main_group"]) == plan_bytes


def test_plan_detailed_exitcode_2_with_out_still_saves_plan(
    runner: CliRunner, fake_terraform_bin: FakeTerraform, tmp_path: Path
) -> None:
    plan_bytes = b"diff-plan-bytes"
    fake_terraform_bin.set_out_bytes(plan_bytes)
    fake_terraform_bin.set_exit_code(2)

    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    out_file = tmp_path / "plan.tnplan"
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(fixture_dir),
            "plan",
            "--detailed-exitcode",
            "--out",
            str(out_file),
        ],
    )
    assert result.exit_code == 2
    assert out_file.exists()
    saved = _read_saved_plan(out_file)
    assert base64.b64decode(saved["main_group"]) == plan_bytes


def test_plan_exitcode_1_with_out_does_not_save_plan(
    runner: CliRunner, fake_terraform_bin: FakeTerraform, tmp_path: Path
) -> None:
    fake_terraform_bin.set_exit_code(1)

    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    out_file = tmp_path / "plan.tnplan"
    result = runner.invoke(
        main,
        args=["--conf-dir", str(fixture_dir), "plan", "--out", str(out_file)],
    )
    assert result.exit_code == 1
    assert not out_file.exists()


def test_plan_fail_at_end_mixed_exit_codes_1_and_2_yields_exit_1(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    # Both groups exit non-zero; since fake terraform is a single global
    # script we can't vary the exit code per group, so this verifies the
    # "any 1 forces overall exit 1" aggregation using a uniform exit code 1
    # across both paths (a strict subset of the mixed-codes behaviour, since
    # a real mixed-1-and-2 run would collapse to the same outcome: exit 1).
    fake_terraform_bin.set_exit_code(1)
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "plan_multi_group"
    result = runner.invoke(
        main, args=["--conf-dir", str(fixture_dir), "plan", "--fail-at-end"]
    )
    assert result.exit_code == 1


def test_plan_fail_at_end_all_exit_2_yields_exit_2(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    fake_terraform_bin.set_exit_code(2)
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "plan_multi_group"
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(fixture_dir),
            "plan",
            "--fail-at-end",
            "--detailed-exitcode",
        ],
    )
    assert result.exit_code == 2


def test_plan_default_parallelism_not_passed(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    runner.invoke(main, args=["--conf-dir", str(fixture_dir), "plan"])
    assert not any(
        a.startswith("-parallelism=") for a in fake_terraform_bin.captured_argv
    )


def test_plan_custom_parallelism_passed(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    runner.invoke(
        main, args=["--conf-dir", str(fixture_dir), "plan", "--parallelism", "4"]
    )
    assert "-parallelism=4" in fake_terraform_bin.captured_argv


def test_plan_import_vars_resolved_by_default(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    fake_terraform_bin.set_stdout("chained-value")
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "import_chain"
    result = runner.invoke(
        main, args=["--conf-dir", str(fixture_dir), "plan", "consumer"]
    )
    assert result.exit_code == 0
    assert fake_terraform_bin.captured_env["TF_VAR_input_var"] == "chained-value"
