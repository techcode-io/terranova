from __future__ import annotations

from pathlib import Path

import pytest
from click.exceptions import Exit

from terranova.exceptions import ExplainedError, InvalidResourcesError
from terranova.utils import AppContext, serde


class TestAppContext:
    def test_create_sets_all_fields(self, tmp_path: Path) -> None:
        ctx = AppContext.create(debug=True, verbose=True, conf_dir=tmp_path)
        assert ctx.debug is True
        assert ctx.verbose is True
        assert ctx.conf_dir == tmp_path

    def test_resources_dir_shared_dir_terraform_dirs_derivation(
        self, tmp_path: Path
    ) -> None:
        ctx = AppContext.create(debug=False, verbose=False, conf_dir=tmp_path)
        assert ctx.resources_dir == tmp_path / "resources"
        assert ctx.shared_dir == tmp_path / "shared"
        assert ctx.terraform_shared_dir == tmp_path / ".terraform"
        assert ctx.terraform_shared_states_dir == tmp_path / ".terraform" / "states"
        assert (
            ctx.terraform_shared_plugin_cache_dir
            == tmp_path / ".terraform" / "plugin-cache"
        )


class TestConsoleLog:
    def test_action_prints_arrow_prefix(
        self, app_context: AppContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        app_context.log.action("do thing")
        assert "do thing" in capsys.readouterr().out

    def test_success_prints_succeeded_prefix(
        self, app_context: AppContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        app_context.log.success("do thing")
        assert "Succeeded to do thing" in capsys.readouterr().out

    def test_failure_prints_failed_prefix_with_single_string_msg(
        self, app_context: AppContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        app_context.log.failure("do thing")
        assert "Failed to do thing" in capsys.readouterr().err

    def test_failure_prints_all_lines_for_list_msg(
        self, app_context: AppContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        app_context.log.failure(["first", "second"])
        err = capsys.readouterr().err
        assert "Failed to first" in err
        assert "second" in err

    def test_failure_with_explained_error_prints_cause_and_resolution(
        self, app_context: AppContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        err = InvalidResourcesError(cause="the cause", resolution="the resolution")
        app_context.log.failure("do thing", err)
        out = capsys.readouterr().err
        assert "Cause: the cause" in out
        assert "Resolution: the resolution" in out

    def test_failure_with_explained_error_no_resolution_omits_resolution_line(
        self, app_context: AppContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        err = ExplainedError(cause="the cause")
        app_context.log.failure("do thing", err)
        out = capsys.readouterr().err
        assert "Cause: the cause" in out
        assert "Resolution:" not in out

    def test_failure_with_generic_exception_prints_details(
        self, app_context: AppContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        app_context.log.failure("do thing", ValueError("boom"))
        out = capsys.readouterr().err
        assert "Details: boom" in out
        assert "Cause:" not in out

    def test_failure_with_no_err_prints_nothing_extra(
        self, app_context: AppContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        app_context.log.failure("do thing", None)
        out = capsys.readouterr().err
        assert "Cause:" not in out
        assert "Details:" not in out

    def test_failure_debug_enabled_prints_exception_traceback(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ctx = AppContext.create(debug=True, verbose=False, conf_dir=tmp_path)
        try:
            raise ValueError("boom")
        except ValueError as err:
            ctx.log.failure("do thing", err)
        out = capsys.readouterr().err
        assert "Traceback" in out

    def test_failure_debug_disabled_does_not_print_traceback(
        self, app_context: AppContext, capsys: pytest.CaptureFixture[str]
    ) -> None:
        try:
            raise ValueError("boom")
        except ValueError as err:
            app_context.log.failure("do thing", err)
        out = capsys.readouterr().err
        assert "Traceback" not in out

    def test_fatal_raises_click_exit_with_given_code(
        self, app_context: AppContext
    ) -> None:
        with pytest.raises(Exit) as exc_info:
            app_context.log.fatal("do thing", raise_exit=3)
        assert exc_info.value.exit_code == 3

    def test_fatal_default_exit_code_is_1(self, app_context: AppContext) -> None:
        with pytest.raises(Exit) as exc_info:
            app_context.log.fatal("do thing")
        assert exc_info.value.exit_code == 1


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
