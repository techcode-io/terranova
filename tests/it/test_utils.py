from __future__ import annotations

from pathlib import Path

import pytest
from click.exceptions import Exit

from terranova.exceptions import ExplainedError, InvalidResourcesError
from terranova.utils import AppContext, log, serde


class TestAppContext:
    def test_fields(self, tmp_path: Path) -> None:
        config = AppContext(conf_dir=tmp_path, verbose=True)
        assert config.verbose is True
        assert config.conf_dir == tmp_path

    def test_resources_dir_shared_dir_terraform_dirs_derivation(
        self, tmp_path: Path
    ) -> None:
        config = AppContext(conf_dir=tmp_path)
        assert config.resources_dir == tmp_path / "resources"
        assert config.shared_dir == tmp_path / "shared"
        assert config.terraform_shared_dir == tmp_path / ".terraform"
        assert config.terraform_shared_states_dir == tmp_path / ".terraform" / "states"
        assert (
            config.terraform_shared_plugin_cache_dir
            == tmp_path / ".terraform" / "plugin-cache"
        )


class TestConsoleLog:
    def test_action_prints_arrow_prefix(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        log.action("do thing")
        assert "do thing" in capsys.readouterr().out

    def test_success_prints_succeeded_prefix(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        log.success("do thing")
        assert "Succeeded to do thing" in capsys.readouterr().out

    def test_failure_prints_failed_prefix_with_single_string_msg(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        log.failure("do thing")
        assert "Failed to do thing" in capsys.readouterr().err

    def test_failure_prints_all_lines_for_list_msg(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        log.failure(["first", "second"])
        err = capsys.readouterr().err
        assert "Failed to first" in err
        assert "second" in err

    def test_failure_with_explained_error_prints_cause_and_resolution(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        err = InvalidResourcesError(cause="the cause", resolution="the resolution")
        log.failure("do thing", err)
        out = capsys.readouterr().err
        assert "Cause: the cause" in out
        assert "Resolution: the resolution" in out

    def test_failure_with_explained_error_no_resolution_omits_resolution_line(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        err = ExplainedError(cause="the cause")
        log.failure("do thing", err)
        out = capsys.readouterr().err
        assert "Cause: the cause" in out
        assert "Resolution:" not in out

    def test_failure_with_generic_exception_prints_details(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        log.failure("do thing", ValueError("boom"))
        out = capsys.readouterr().err
        assert "Details: boom" in out
        assert "Cause:" not in out

    def test_failure_with_no_err_prints_nothing_extra(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        log.failure("do thing", None)
        out = capsys.readouterr().err
        assert "Cause:" not in out
        assert "Details:" not in out

    def test_failure_debug_enabled_prints_exception_traceback(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        log.configure(debug=True)
        try:
            raise ValueError("boom")
        except ValueError as err:
            log.failure("do thing", err)
        out = capsys.readouterr().err
        assert "Traceback" in out

    def test_failure_debug_disabled_does_not_print_traceback(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        try:
            raise ValueError("boom")
        except ValueError as err:
            log.failure("do thing", err)
        out = capsys.readouterr().err
        assert "Traceback" not in out

    def test_fatal_raises_click_exit_with_given_code(self) -> None:
        with pytest.raises(Exit) as exc_info:
            log.fatal("do thing", raise_exit=3)
        assert exc_info.value.exit_code == 3

    def test_fatal_default_exit_code_is_1(self) -> None:
        with pytest.raises(Exit) as exc_info:
            log.fatal("do thing")
        assert exc_info.value.exit_code == 1

    def test_render_writes_component_to_stdout(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        log.render("hello table")
        captured = capsys.readouterr()
        assert "hello table" in captured.out
        assert captured.err == ""

    def test_render_err_writes_component_to_stderr(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        log.render("oops", err=True)
        captured = capsys.readouterr()
        assert "oops" in captured.err
        assert captured.out == ""


class TestSerdeDecorator:
    def test_serde_passthrough_disables_type_check(self) -> None:
        from dataclasses import dataclass

        from serde import from_dict

        @serde
        @dataclass(frozen=True)
        class Sample:
            value: str

        # type_check=disabled means a mismatched type is not rejected.
        result = from_dict(Sample, {"value": 123})
        assert result.value == 123
