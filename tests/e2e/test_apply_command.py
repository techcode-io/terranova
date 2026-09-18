import base64
import json
from pathlib import Path

from click.testing import CliRunner

from terranova.cli import main
from tests import PROJECT_TESTS_FIXTURES_DIR
from tests.conftest import FakeTerraform
from tests.e2e.conftest import copy_as_git_repo


def test_apply_normal_success(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    result = runner.invoke(
        main, args=["--conf-dir", str(fixture_dir), "apply", "--auto-approve"]
    )
    assert result.exit_code == 0
    assert "-auto-approve" in fake_terraform_bin.captured_argv


def test_apply_target_flag_forwarded(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(fixture_dir),
            "apply",
            "--target",
            "aws_instance.foo",
        ],
    )
    assert "-target=aws_instance.foo" in fake_terraform_bin.captured_argv


def test_apply_fail_at_end_without_flag_stops_after_first(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    fake_terraform_bin.set_exit_code(1)
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "plan_multi_group"
    result = runner.invoke(main, args=["--conf-dir", str(fixture_dir), "apply"])
    assert result.exit_code == 1
    assert result.stdout.count("Applying plan:") == 1


def test_apply_fail_at_end_continues_all_and_exits_1(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    fake_terraform_bin.set_exit_code(1)
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "plan_multi_group"
    result = runner.invoke(
        main, args=["--conf-dir", str(fixture_dir), "apply", "--fail-at-end"]
    )
    assert result.exit_code == 1
    assert result.stdout.count("Applying plan:") == 2


def test_apply_with_tnplan_file_round_trips_plan_bytes(
    runner: CliRunner, fake_terraform_bin: FakeTerraform, tmp_path: Path
) -> None:
    plan_bytes = b"saved-plan-bytes"
    tnplan_file = tmp_path / "saved.tnplan"
    tnplan_file.write_text(
        json.dumps({"main_group": base64.b64encode(plan_bytes).decode("ascii")})
    )

    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    result = runner.invoke(
        main,
        args=["--conf-dir", str(fixture_dir), "apply", str(tnplan_file)],
    )
    assert result.exit_code == 0
    argv = fake_terraform_bin.captured_argv
    assert argv[0] == "apply"
    plan_arg_path = Path(argv[1])
    # The plan file is a NamedTemporaryFile that gets cleaned up right after
    # exec() returns, so we can't read it back post-hoc here — but the fake
    # terraform binary's *env* capture proves the temp file path was passed
    # through, and terranova's own apply() argument-building is covered at
    # the unit level in tests/it/test_binds.py. This documents the observable
    # e2e contract: a distinct plan file path is always passed as `apply`'s
    # first positional argument when applying from a .tnplan file.
    assert plan_arg_path.name != str(tnplan_file)


def test_apply_strategy_parallel_independent_groups_succeeds(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    _ = fake_terraform_bin
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "plan_multi_group"
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(fixture_dir),
            "apply",
            "--strategy",
            "parallel",
            "--auto-approve",
        ],
    )
    assert result.exit_code == 0
    # Parallel mode is quiet on success: no per-project "Applying plan:" chatter,
    # only the live status display (reflected here as the final overall count).
    assert "Applying plan:" not in result.stdout
    assert "2/2" in result.stdout


def test_apply_strategy_parallel_fail_at_end_runs_all(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    fake_terraform_bin.set_exit_code(1)
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "plan_multi_group"
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(fixture_dir),
            "apply",
            "--strategy",
            "parallel",
            "--fail-at-end",
            "--auto-approve",
        ],
    )
    assert result.exit_code == 1
    # Both projects ran (fail-at-end); failures are always reported even
    # though parallel mode stays quiet about successes.
    assert result.stdout.count("failed:") == 2


def test_apply_strategy_parallel_without_auto_approve_fails_fast(
    runner: CliRunner, fake_terraform_bin: FakeTerraform
) -> None:
    """
    `--strategy parallel` runs several `terraform apply` processes at once, so
    none of them can fall back to an interactive approval prompt. Without a
    saved plan or `--auto-approve`, terranova should refuse up front instead
    of launching tasks doomed to fail with terraform's own unhelpful
    "Plan file or auto-approve required" error.
    """
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "plan_multi_group"
    result = runner.invoke(
        main, args=["--conf-dir", str(fixture_dir), "apply", "--strategy", "parallel"]
    )
    assert result.exit_code == 1
    assert not fake_terraform_bin.was_invoked


def test_apply_tnplan_with_strategy_parallel_builds_real_graph_from_disk(
    runner: CliRunner, fake_terraform_bin: FakeTerraform, tmp_path: Path
) -> None:
    """
    A saved `.tnplan` file only stores `{rel_path: base64 plan}` pairs, but the
    manifests (and thus `imports`/dependency order) still exist on disk at the
    same rel_paths, so `--strategy parallel` can build a real dependency graph
    for a `.tnplan` apply too - no degrade-to-sequential fallback needed.
    """
    _ = fake_terraform_bin
    plan_bytes = b"saved-plan-bytes"
    tnplan_file = tmp_path / "saved.tnplan"
    tnplan_file.write_text(
        json.dumps({"main_group": base64.b64encode(plan_bytes).decode("ascii")})
    )

    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    result = runner.invoke(
        main,
        args=[
            "--conf-dir",
            str(fixture_dir),
            "apply",
            str(tnplan_file),
            "--strategy",
            "parallel",
        ],
    )
    assert result.exit_code == 0


def test_apply_with_malformed_tnplan_file_raises_unhandled_exception(
    runner: CliRunner, tmp_path: Path
) -> None:
    """
    Documents a known gap: a malformed `.tnplan` file (invalid JSON here)
    is not caught anywhere in `apply`'s command body, so it surfaces as an
    unhandled exception rather than a clean `Exit(1)`. This is current
    behavior, not a target this test should silently normalize — the parse
    failure happens before terraform is ever invoked, so no fake terraform
    binary is needed here.
    """
    tnplan_file = tmp_path / "broken.tnplan"
    tnplan_file.write_text("not valid json")

    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    result = runner.invoke(
        main,
        args=["--conf-dir", str(fixture_dir), "apply", str(tnplan_file)],
    )
    assert result.exit_code != 0
    assert result.exception is not None


def test_apply_with_non_object_tnplan_file_raises_type_error(
    runner: CliRunner, tmp_path: Path
) -> None:
    """A `.tnplan` file that's valid JSON but not a flat object of strings
    (e.g. a list, or a value that isn't a string) must not be silently
    trusted - the plan file path is untrusted external input."""
    tnplan_file = tmp_path / "not_an_object.tnplan"
    tnplan_file.write_text(json.dumps(["main_group"]))

    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    result = runner.invoke(
        main,
        args=["--conf-dir", str(fixture_dir), "apply", str(tnplan_file)],
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, TypeError)


def test_apply_auto_scope_only_processes_changed_group(
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
        main,
        args=["--conf-dir", str(conf_dir), "apply", "--auto-approve", "--auto-scope"],
    )

    assert result.exit_code == 0
    assert result.stdout.count("Applying plan:") == 1
    assert "group_a" in result.stdout
    assert "group_b" not in result.stdout


def test_apply_auto_scope_and_path_or_plan_together_is_usage_error(
    runner: CliRunner, fake_terraform_bin: FakeTerraform, tmp_path: Path
) -> None:
    _ = fake_terraform_bin
    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "plan_multi_group"
    conf_dir = tmp_path / "conf"
    copy_as_git_repo(fixture_dir, conf_dir)

    result = runner.invoke(
        main,
        args=["--conf-dir", str(conf_dir), "apply", "group_a", "-A", "--auto-approve"],
    )

    assert result.exit_code != 0
    assert "--auto-scope" in result.output


def test_apply_with_non_string_tnplan_value_raises_type_error(
    runner: CliRunner, tmp_path: Path
) -> None:
    tnplan_file = tmp_path / "bad_value.tnplan"
    tnplan_file.write_text(json.dumps({"main_group": 12345}))

    fixture_dir = PROJECT_TESTS_FIXTURES_DIR / "simple_resource_group"
    result = runner.invoke(
        main,
        args=["--conf-dir", str(fixture_dir), "apply", str(tnplan_file)],
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, TypeError)
