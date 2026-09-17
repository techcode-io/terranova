from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.exceptions import Exit

from terranova.binds import (
    ChangeSummary,
    ResourceChange,
    Terraform,
    TerraformChangeError,
)
from terranova.exceptions import InvalidResourcesError
from terranova.utils import AppContext
from tests.conftest import FakeTerraform


class TestTerraformConstruction:
    def test_missing_terraform_binary_triggers_fatal_exit(
        self,
        app_context: AppContext,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        empty_bin = tmp_path / "empty_bin"
        empty_bin.mkdir()
        monkeypatch.setenv("PATH", str(empty_bin))
        with pytest.raises(Exit):
            Terraform(app_context, tmp_path)

    def test_plugin_cache_dir_created(
        self,
        app_context: AppContext,
        tmp_path: Path,
        fake_terraform_bin: FakeTerraform,
    ) -> None:
        _ = fake_terraform_bin
        Terraform(app_context, tmp_path)
        assert app_context.terraform_shared_plugin_cache_dir.exists()


class TestTerraformCreateEnvFiltering:
    def test_allowed_prefixes_and_exact_names_forwarded(
        self,
        app_context: AppContext,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_terraform_bin: FakeTerraform,
    ) -> None:
        monkeypatch.setenv("TF_FOO", "tf_value")
        monkeypatch.setenv("TERRANOVA_FOO", "terranova_value")
        monkeypatch.setenv("AWS_FOO", "aws_value")
        monkeypatch.setenv("ASDF_FOO", "asdf_value")
        monkeypatch.setenv("GCLOUD_PROJECT", "gcloud_value")
        monkeypatch.setenv("HOME", "/home/test")
        terraform = Terraform(app_context, tmp_path)
        terraform.graph()
        env = fake_terraform_bin.captured_env
        assert env["TF_FOO"] == "tf_value"
        assert env["TERRANOVA_FOO"] == "terranova_value"
        assert env["AWS_FOO"] == "aws_value"
        assert env["ASDF_FOO"] == "asdf_value"
        assert env["GCLOUD_PROJECT"] == "gcloud_value"
        assert env["HOME"] == "/home/test"

    def test_disallowed_env_vars_filtered_out(
        self,
        app_context: AppContext,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_terraform_bin: FakeTerraform,
    ) -> None:
        monkeypatch.setenv("RANDOM_SECRET", "xyz")
        terraform = Terraform(app_context, tmp_path)
        terraform.graph()
        assert "RANDOM_SECRET" not in fake_terraform_bin.captured_env

    def test_gcp_env_vars_are_exact_match_not_prefix(
        self,
        app_context: AppContext,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_terraform_bin: FakeTerraform,
    ) -> None:
        """
        The GCP/gcloud allowlist (`GOOGLE_CLOUD_PROJECT`, `GCLOUD_PROJECT`,
        etc.) is a fixed set of *exact* names, unlike TF_/TERRANOVA_/AWS_/
        ASDF_ which are prefix-matched. This guards against a future
        refactor accidentally turning it into a prefix match.
        """
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "exact")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT_EXTRA", "should_not_match")
        terraform = Terraform(app_context, tmp_path)
        terraform.graph()
        env = fake_terraform_bin.captured_env
        assert env.get("GOOGLE_CLOUD_PROJECT") == "exact"
        assert "GOOGLE_CLOUD_PROJECT_EXTRA" not in env

    def test_tf_var_prefix_injected_from_variables(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        terraform = Terraform(app_context, tmp_path, variables={"region": "eu-west-1"})
        terraform.graph()
        assert fake_terraform_bin.captured_env["TF_VAR_region"] == "eu-west-1"

    def test_tf_plugin_cache_dir_always_set(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        terraform = Terraform(app_context, tmp_path)
        terraform.graph()
        assert "TF_PLUGIN_CACHE_DIR" in fake_terraform_bin.captured_env

    def test_tf_log_debug_only_when_verbose(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        ctx = AppContext.create(debug=False, verbose=True, conf_dir=tmp_path)
        terraform = Terraform(ctx, tmp_path)
        terraform.graph()
        assert fake_terraform_bin.captured_env.get("TF_LOG") == "DEBUG"

    def test_tf_log_absent_when_not_verbose(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        terraform = Terraform(app_context, tmp_path)
        terraform.graph()
        assert "TF_LOG" not in fake_terraform_bin.captured_env


class TestTerraformInit:
    def test_init_default_args(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(app_context, tmp_path).init()
        assert fake_terraform_bin.captured_argv == ["init"]

    def test_init_all_flags(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(app_context, tmp_path).init(
            backend_config={"key": "v", "k2": "v2"},
            migrate_state=True,
            no_backend=True,
            reconfigure=True,
            upgrade=True,
        )
        argv = fake_terraform_bin.captured_argv
        assert argv[0] == "init"
        assert "-reconfigure" in argv
        assert "-upgrade" in argv
        assert "-migrate-state" in argv
        assert "-backend=false" in argv
        assert "-backend-config=key=v" in argv
        assert "-backend-config=k2=v2" in argv


class TestTerraformValidate:
    def test_validate_success_returns_valid_result(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        fake_terraform_bin.set_exit_code(0)
        result = Terraform(app_context, tmp_path).validate()
        assert result.valid
        assert result.error_count == 0
        assert result.diagnostics == ()

    def test_validate_failure_returns_invalid_result_with_diagnostics(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        fake_terraform_bin.set_exit_code(1)
        result = Terraform(app_context, tmp_path).validate()
        assert not result.valid
        assert result.error_count == 1
        assert len(result.diagnostics) == 1
        assert result.diagnostics[0].severity == "error"

    def test_validate_appends_json_flag(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(app_context, tmp_path).validate()
        assert "-json" in fake_terraform_bin.captured_argv

    def test_validate_unparseable_report_raises_invalid_resources_error(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        fake_terraform_bin.set_stdout("not valid json")
        with pytest.raises(InvalidResourcesError):
            Terraform(app_context, tmp_path).validate()

    def test_validate_non_object_report_raises_invalid_resources_error(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        fake_terraform_bin.set_stdout("[1, 2, 3]")
        with pytest.raises(InvalidResourcesError):
            Terraform(app_context, tmp_path).validate()


class TestTerraformPlan:
    def _plan(
        self,
        app_context: AppContext,
        tmp_path: Path,
        *,
        input: bool = True,
        no_color: bool = False,
        parallelism: int | None = 10,
        detailed_exitcode: bool = False,
        out: Path | None = None,
    ) -> ChangeSummary:
        return Terraform(app_context, tmp_path).plan(
            input=input,
            no_color=no_color,
            parallelism=parallelism,
            detailed_exitcode=detailed_exitcode,
            out=out,
        )

    def test_plan_default_parallelism_omitted(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        self._plan(app_context, tmp_path)
        assert not any(
            a.startswith("-parallelism=") for a in fake_terraform_bin.captured_argv
        )

    def test_plan_custom_parallelism_included(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        self._plan(app_context, tmp_path, parallelism=4)
        assert "-parallelism=4" in fake_terraform_bin.captured_argv

    def test_plan_parallelism_none_omitted(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        self._plan(app_context, tmp_path, parallelism=None)
        assert not any(
            a.startswith("-parallelism=") for a in fake_terraform_bin.captured_argv
        )

    def test_plan_input_true(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        self._plan(app_context, tmp_path, input=True)
        assert "-input=true" in fake_terraform_bin.captured_argv

    def test_plan_input_false(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        self._plan(app_context, tmp_path, input=False)
        assert "-input=false" in fake_terraform_bin.captured_argv

    def test_plan_no_color_flag(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        self._plan(app_context, tmp_path, no_color=True)
        assert "-no-color" in fake_terraform_bin.captured_argv

    def test_plan_detailed_exitcode_flag(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        self._plan(app_context, tmp_path, detailed_exitcode=True)
        assert "-detailed-exitcode" in fake_terraform_bin.captured_argv

    def test_plan_out_path_included(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        out_path = tmp_path / "plan.tfplan"
        self._plan(app_context, tmp_path, out=out_path)
        assert f"-out={out_path.as_posix()}" in fake_terraform_bin.captured_argv

    def test_plan_appends_json_flag(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        self._plan(app_context, tmp_path)
        assert "-json" in fake_terraform_bin.captured_argv

    def test_plan_returns_change_summary(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        fake_terraform_bin.set_stdout(
            '{"type":"change_summary","changes":{"add":2,"change":1,"remove":0}}\n'
        )
        summary = self._plan(app_context, tmp_path)
        assert summary.to_add == 2
        assert summary.to_change == 1
        assert summary.to_destroy == 0

    def test_plan_counts_planned_change_events(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        fake_terraform_bin.set_stdout(
            "\n".join(
                [
                    '{"type":"planned_change","change":{"action":"create"}}',
                    '{"type":"planned_change","change":{"action":"update"}}',
                    '{"type":"planned_change","change":{"action":"delete"}}',
                    '{"type":"planned_change","change":{"action":"create"}}',
                ]
            )
        )
        summary = self._plan(app_context, tmp_path)
        assert summary.to_add == 2
        assert summary.to_change == 1
        assert summary.to_destroy == 1

    def test_plan_collects_resource_changes_from_planned_change_events(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        fake_terraform_bin.set_stdout(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "planned_change",
                            "change": {
                                "resource": {"addr": "aws_instance.foo"},
                                "action": "create",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "planned_change",
                            "change": {
                                "resource": {"addr": "aws_instance.bar"},
                                "action": "replace",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "planned_change",
                            "change": {
                                "resource": {"addr": "data.aws_ami.baz"},
                                "action": "read",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "planned_change",
                            "change": {
                                "resource": {"addr": "aws_instance.unchanged"},
                                "action": "no-op",
                            },
                        }
                    ),
                ]
            )
        )
        summary = self._plan(app_context, tmp_path)
        assert summary.resources == (
            ResourceChange(address="aws_instance.foo", action="create"),
            ResourceChange(address="aws_instance.bar", action="replace"),
        )

    def test_plan_error_raises_terraform_change_error_with_summary(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        fake_terraform_bin.set_stdout(
            '{"type":"diagnostic","@level":"error","@message":"boom"}\n'
        )
        fake_terraform_bin.set_exit_code(1)
        with pytest.raises(TerraformChangeError) as exc_info:
            self._plan(app_context, tmp_path)
        assert exc_info.value.exit_code == 1
        assert exc_info.value.summary.has_errors
        assert "boom" in exc_info.value.summary.diagnostics[0]

    def test_plan_error_with_no_json_diagnostic_falls_back_to_raw_output(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        """
        Terraform can fail without ever emitting a JSON `diagnostic` event (a
        crash before -json mode takes effect, a plain-text stderr message,
        ...). The failure must still carry an explanation instead of a bare,
        content-free `ChangeSummary`.
        """
        fake_terraform_bin.set_stdout("panic: unexpected nil pointer\nstack trace...")
        fake_terraform_bin.set_exit_code(1)
        with pytest.raises(TerraformChangeError) as exc_info:
            self._plan(app_context, tmp_path)
        assert exc_info.value.summary.has_errors
        assert "panic: unexpected nil pointer" in exc_info.value.summary.diagnostics[0]

    def test_plan_error_with_no_output_at_all_still_explains_exit_code(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        fake_terraform_bin.set_exit_code(1)
        with pytest.raises(TerraformChangeError) as exc_info:
            self._plan(app_context, tmp_path)
        assert exc_info.value.summary.has_errors
        assert "exited with code 1" in exc_info.value.summary.diagnostics[0]

    def test_plan_error_diagnostic_includes_nested_detail_not_just_summary(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        """
        Regression test: terraform's diagnostic events carry the generic
        summary at top-level (`@message`) and the actually useful explanation
        nested under `diagnostic.detail` (e.g. an external program's stderr).
        Only surfacing `@message` produces content-free, repeated-looking
        failures like three identical "External Program Execution Failed"
        lines with no way to tell them apart.
        """
        diagnostic_line = json.dumps(
            {
                "@level": "error",
                "@message": "Error: External Program Execution Failed",
                "type": "diagnostic",
                "diagnostic": {
                    "severity": "error",
                    "summary": "External Program Execution Failed",
                    "detail": (
                        "call to external.foo failed with unexpected error: "
                        'exit status 1. stderr: "connection refused: 10.0.0.5:443"'
                    ),
                },
            }
        )
        fake_terraform_bin.set_stdout(diagnostic_line + "\n")
        fake_terraform_bin.set_exit_code(1)
        with pytest.raises(TerraformChangeError) as exc_info:
            self._plan(app_context, tmp_path)
        message = exc_info.value.summary.diagnostics[0]
        assert "External Program Execution Failed" in message
        assert "connection refused: 10.0.0.5:443" in message


class TestTerraformApply:
    def test_apply_default_no_flags_is_interactive(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        """
        Without a saved plan or `-auto-approve`, terraform must be free to
        prompt for interactive approval on a real terminal - `-json` would
        disable that prompt outright, so it must be omitted here.
        """
        summary = Terraform(app_context, tmp_path).apply()
        argv = fake_terraform_bin.captured_argv
        assert argv[0] == "apply"
        assert "-json" not in argv
        assert summary == ChangeSummary()

    def test_apply_with_plan_arg(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(app_context, tmp_path).apply(plan="path/to/plan")
        argv = fake_terraform_bin.captured_argv
        assert "path/to/plan" in argv
        assert "-json" in argv

    def test_apply_auto_approve_flag(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(app_context, tmp_path).apply(auto_approve=True)
        argv = fake_terraform_bin.captured_argv
        assert "-auto-approve" in argv
        assert "-json" in argv

    def test_apply_target_flag(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(app_context, tmp_path).apply(
            auto_approve=True, target="aws_instance.foo"
        )
        assert "-target=aws_instance.foo" in fake_terraform_bin.captured_argv

    def test_apply_returns_change_summary(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        fake_terraform_bin.set_stdout(
            '{"type":"change_summary","changes":{"add":1,"change":0,"remove":0}}\n'
        )
        summary = Terraform(app_context, tmp_path).apply(auto_approve=True)
        assert summary.to_add == 1

    def test_apply_error_raises_terraform_change_error_with_summary(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        fake_terraform_bin.set_stdout(
            '{"type":"diagnostic","@level":"error","@message":"apply boom"}\n'
        )
        fake_terraform_bin.set_exit_code(1)
        with pytest.raises(TerraformChangeError) as exc_info:
            Terraform(app_context, tmp_path).apply(auto_approve=True)
        assert exc_info.value.exit_code == 1
        assert "apply boom" in exc_info.value.summary.diagnostics[0]


class TestChangeSummaryRender:
    def test_no_diagnostics(self) -> None:
        summary = ChangeSummary(to_add=1, to_change=2, to_destroy=3)
        rendered = summary.render("group_a")
        assert "group_a" in rendered
        assert "1 to add" in rendered
        assert "2 to change" in rendered
        assert "3 to destroy" in rendered

    def test_resource_changes_rendered_under_summary_line(self) -> None:
        summary = ChangeSummary(
            to_add=1,
            to_change=0,
            to_destroy=1,
            resources=(
                ResourceChange(address="aws_instance.foo", action="create"),
                ResourceChange(address="aws_instance.bar", action="delete"),
            ),
        )
        rendered = summary.render("group_a")
        lines = rendered.splitlines()
        assert lines[0].startswith("group_a: Plan:")
        assert "aws_instance.foo" in lines[1]
        assert "+" in lines[1]
        assert "aws_instance.bar" in lines[2]
        assert "-" in lines[2]

    def test_with_diagnostics(self) -> None:
        summary = ChangeSummary(diagnostics=("something went wrong",))
        rendered = summary.render("group_a")
        assert "group_a" in rendered
        assert "something went wrong" in rendered

    def test_multiline_diagnostic_indents_every_line(self) -> None:
        summary = ChangeSummary(
            diagnostics=("summary line\ndetail line 1\ndetail line 2",)
        )
        rendered = summary.render("group_a")
        lines = rendered.splitlines()
        assert lines[3] == "  summary line"
        assert lines[4] == "  detail line 1"
        assert lines[5] == "  detail line 2"

    def test_diagnostic_header_ruled_and_contains_rel_path(self) -> None:
        summary = ChangeSummary(diagnostics=("boom",))
        rendered = summary.render("group_a")
        lines = rendered.splitlines()
        assert lines[0] == lines[2]  # matching rule above and below the header
        assert "group_a" in lines[1]


class TestTerraformOutput:
    def test_output_captures_stdout_via_stringio(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        fake_terraform_bin.set_stdout("captured-value")
        result = Terraform(app_context, tmp_path).output("some_name")
        assert result == "captured-value"
        assert fake_terraform_bin.captured_argv == ["output", "-raw", "some_name"]


class TestTerraformOtherCommands:
    def test_graph_invokes_graph_arg(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(app_context, tmp_path).graph()
        assert fake_terraform_bin.captured_argv == ["graph"]

    def test_taint_invokes_taint_address_args(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(app_context, tmp_path).taint("aws_instance.foo")
        assert fake_terraform_bin.captured_argv == ["taint", "aws_instance.foo"]

    def test_untaint_invokes_untaint_address_args(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(app_context, tmp_path).untaint("aws_instance.foo")
        assert fake_terraform_bin.captured_argv == ["untaint", "aws_instance.foo"]

    def test_define_invokes_import_address_identifier_args(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(app_context, tmp_path).define("aws_instance.foo", "i-1234")
        assert fake_terraform_bin.captured_argv == [
            "import",
            "aws_instance.foo",
            "i-1234",
        ]

    def test_destroy_invokes_destroy_arg(
        self, app_context: AppContext, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(app_context, tmp_path).destroy()
        assert fake_terraform_bin.captured_argv == ["destroy"]
