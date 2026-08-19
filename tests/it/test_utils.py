from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from click.exceptions import Exit

from terranova.exceptions import ExplainedError, InvalidResourcesError
from terranova.utils import SharedContext, serde


class TestSharedContext:
    def test_getters_raise_keyerror_before_init(self) -> None:
        # SharedContext is a process-global singleton with no reset API;
        # directly clear its backing dict to simulate pre-init state, then
        # let the autouse _reset_shared_context fixture re-init it for the
        # next test.
        underlying = cast(
            "dict[str, object]",
            SharedContext._SharedContext__UNDERLYING,  # pyright: ignore[reportAttributeAccessIssue]
        )
        underlying.clear()
        with pytest.raises(KeyError):
            SharedContext.console()

    def test_init_sets_all_fields(self, tmp_path: Path) -> None:
        SharedContext.init(debug=True, verbose=True, conf_dir=tmp_path)
        assert SharedContext.is_debug_enabled() is True
        assert SharedContext.is_verbose_enabled() is True
        assert SharedContext.conf_dir() == tmp_path

    def test_resources_dir_shared_dir_terraform_dirs_derivation(
        self, tmp_path: Path
    ) -> None:
        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        assert SharedContext.resources_dir() == tmp_path / "resources"
        assert SharedContext.shared_dir() == tmp_path / "shared"
        assert SharedContext.terraform_shared_dir() == tmp_path / ".terraform"
        assert (
            SharedContext.terraform_shared_states_dir()
            == tmp_path / ".terraform" / "states"
        )
        assert (
            SharedContext.terraform_shared_plugin_cache_dir()
            == tmp_path / ".terraform" / "plugin-cache"
        )


class TestLog:
    def test_action_prints_arrow_prefix(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from terranova.utils import Log

        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        Log.action("do thing")
        assert "do thing" in capsys.readouterr().out

    def test_success_prints_succeeded_prefix(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from terranova.utils import Log

        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        Log.success("do thing")
        assert "Succeeded to do thing" in capsys.readouterr().out

    def test_failure_prints_failed_prefix_with_single_string_msg(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from terranova.utils import Log

        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        Log.failure("do thing")
        assert "Failed to do thing" in capsys.readouterr().err

    def test_failure_prints_all_lines_for_list_msg(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from terranova.utils import Log

        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        Log.failure(["first", "second"])
        err = capsys.readouterr().err
        assert "Failed to first" in err
        assert "second" in err

    def test_failure_with_explained_error_prints_cause_and_resolution(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from terranova.utils import Log

        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        err = InvalidResourcesError(cause="the cause", resolution="the resolution")
        Log.failure("do thing", err)
        out = capsys.readouterr().err
        assert "Cause: the cause" in out
        assert "Resolution: the resolution" in out

    def test_failure_with_explained_error_no_resolution_omits_resolution_line(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from terranova.utils import Log

        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        err = ExplainedError(cause="the cause")
        Log.failure("do thing", err)
        out = capsys.readouterr().err
        assert "Cause: the cause" in out
        assert "Resolution:" not in out

    def test_failure_with_generic_exception_prints_details(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from terranova.utils import Log

        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        Log.failure("do thing", ValueError("boom"))
        out = capsys.readouterr().err
        assert "Details: boom" in out
        assert "Cause:" not in out

    def test_failure_with_no_err_prints_nothing_extra(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from terranova.utils import Log

        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        Log.failure("do thing", None)
        out = capsys.readouterr().err
        assert "Cause:" not in out
        assert "Details:" not in out

    def test_failure_debug_enabled_prints_exception_traceback(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from terranova.utils import Log

        SharedContext.init(debug=True, verbose=False, conf_dir=tmp_path)
        try:
            raise ValueError("boom")
        except ValueError as err:
            Log.failure("do thing", err)
        out = capsys.readouterr().err
        assert "Traceback" in out

    def test_failure_debug_disabled_does_not_print_traceback(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from terranova.utils import Log

        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        try:
            raise ValueError("boom")
        except ValueError as err:
            Log.failure("do thing", err)
        out = capsys.readouterr().err
        assert "Traceback" not in out

    def test_fatal_raises_click_exit_with_given_code(self, tmp_path: Path) -> None:
        from terranova.utils import Log

        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        with pytest.raises(Exit) as exc_info:
            Log.fatal("do thing", raise_exit=3)
        assert exc_info.value.exit_code == 3

    def test_fatal_default_exit_code_is_1(self, tmp_path: Path) -> None:
        from terranova.utils import Log

        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        with pytest.raises(Exit) as exc_info:
            Log.fatal("do thing")
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
