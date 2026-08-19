from __future__ import annotations

from pathlib import Path

import pytest
from click.exceptions import Exit

from terranova.binds import Terraform
from terranova.exceptions import InvalidResourcesError
from terranova.utils import SharedContext
from tests.conftest import FakeTerraform


class TestTerraformConstruction:
    def test_missing_terraform_binary_triggers_fatal_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty_bin = tmp_path / "empty_bin"
        empty_bin.mkdir()
        monkeypatch.setenv("PATH", str(empty_bin))
        with pytest.raises(Exit):
            Terraform(tmp_path)

    def test_plugin_cache_dir_created(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        _ = fake_terraform_bin
        Terraform(tmp_path)
        assert SharedContext.terraform_shared_plugin_cache_dir().exists()


class TestTerraformCreateEnvFiltering:
    def test_allowed_prefixes_and_exact_names_forwarded(
        self,
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
        terraform = Terraform(tmp_path)
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
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_terraform_bin: FakeTerraform,
    ) -> None:
        monkeypatch.setenv("RANDOM_SECRET", "xyz")
        terraform = Terraform(tmp_path)
        terraform.graph()
        assert "RANDOM_SECRET" not in fake_terraform_bin.captured_env

    def test_gcp_env_vars_are_exact_match_not_prefix(
        self,
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
        terraform = Terraform(tmp_path)
        terraform.graph()
        env = fake_terraform_bin.captured_env
        assert env.get("GOOGLE_CLOUD_PROJECT") == "exact"
        assert "GOOGLE_CLOUD_PROJECT_EXTRA" not in env

    def test_tf_var_prefix_injected_from_variables(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        terraform = Terraform(tmp_path, variables={"region": "eu-west-1"})
        terraform.graph()
        assert fake_terraform_bin.captured_env["TF_VAR_region"] == "eu-west-1"

    def test_tf_plugin_cache_dir_always_set(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        terraform = Terraform(tmp_path)
        terraform.graph()
        assert "TF_PLUGIN_CACHE_DIR" in fake_terraform_bin.captured_env

    def test_tf_log_debug_only_when_verbose(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        SharedContext.init(debug=False, verbose=True, conf_dir=tmp_path)
        terraform = Terraform(tmp_path)
        terraform.graph()
        assert fake_terraform_bin.captured_env.get("TF_LOG") == "DEBUG"

    def test_tf_log_absent_when_not_verbose(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        SharedContext.init(debug=False, verbose=False, conf_dir=tmp_path)
        terraform = Terraform(tmp_path)
        terraform.graph()
        assert "TF_LOG" not in fake_terraform_bin.captured_env


class TestTerraformInit:
    def test_init_default_args(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(tmp_path).init()
        assert fake_terraform_bin.captured_argv == ["init"]

    def test_init_all_flags(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(tmp_path).init(
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
    def test_validate_success_no_raise(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        fake_terraform_bin.set_exit_code(0)
        Terraform(tmp_path).validate()

    def test_validate_failure_wraps_error_return_code(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        fake_terraform_bin.set_exit_code(1)
        with pytest.raises(InvalidResourcesError):
            Terraform(tmp_path).validate()


class TestTerraformPlan:
    def _plan(
        self,
        tmp_path: Path,
        *,
        compact_warnings: bool = False,
        input: bool = True,
        no_color: bool = False,
        parallelism: int | None = 10,
        detailed_exitcode: bool = False,
        out: Path | None = None,
    ) -> None:
        Terraform(tmp_path).plan(
            compact_warnings=compact_warnings,
            input=input,
            no_color=no_color,
            parallelism=parallelism,
            detailed_exitcode=detailed_exitcode,
            out=out,
        )

    def test_plan_default_parallelism_omitted(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        self._plan(tmp_path)
        assert not any(
            a.startswith("-parallelism=") for a in fake_terraform_bin.captured_argv
        )

    def test_plan_custom_parallelism_included(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        self._plan(tmp_path, parallelism=4)
        assert "-parallelism=4" in fake_terraform_bin.captured_argv

    def test_plan_parallelism_none_omitted(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        self._plan(tmp_path, parallelism=None)
        assert not any(
            a.startswith("-parallelism=") for a in fake_terraform_bin.captured_argv
        )

    def test_plan_input_true(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        self._plan(tmp_path, input=True)
        assert "-input=true" in fake_terraform_bin.captured_argv

    def test_plan_input_false(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        self._plan(tmp_path, input=False)
        assert "-input=false" in fake_terraform_bin.captured_argv

    def test_plan_compact_warnings_flag(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        self._plan(tmp_path, compact_warnings=True)
        assert "-compact-warnings" in fake_terraform_bin.captured_argv

    def test_plan_no_color_flag(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        self._plan(tmp_path, no_color=True)
        assert "-no-color" in fake_terraform_bin.captured_argv

    def test_plan_detailed_exitcode_flag(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        self._plan(tmp_path, detailed_exitcode=True)
        assert "-detailed-exitcode" in fake_terraform_bin.captured_argv

    def test_plan_out_path_included(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        out_path = tmp_path / "plan.tfplan"
        self._plan(tmp_path, out=out_path)
        assert f"-out={out_path.as_posix()}" in fake_terraform_bin.captured_argv


class TestTerraformApply:
    def test_apply_default_no_flags(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(tmp_path).apply()
        assert fake_terraform_bin.captured_argv == ["apply"]

    def test_apply_with_plan_arg(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(tmp_path).apply(plan="path/to/plan")
        assert fake_terraform_bin.captured_argv == ["apply", "path/to/plan"]

    def test_apply_auto_approve_flag(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(tmp_path).apply(auto_approve=True)
        assert "-auto-approve" in fake_terraform_bin.captured_argv

    def test_apply_target_flag(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(tmp_path).apply(target="aws_instance.foo")
        assert "-target=aws_instance.foo" in fake_terraform_bin.captured_argv


class TestTerraformOutput:
    def test_output_captures_stdout_via_stringio(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        fake_terraform_bin.set_stdout("captured-value")
        result = Terraform(tmp_path).output("some_name")
        assert result == "captured-value"
        assert fake_terraform_bin.captured_argv == ["output", "-raw", "some_name"]


class TestTerraformOtherCommands:
    def test_graph_invokes_graph_arg(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(tmp_path).graph()
        assert fake_terraform_bin.captured_argv == ["graph"]

    def test_taint_invokes_taint_address_args(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(tmp_path).taint("aws_instance.foo")
        assert fake_terraform_bin.captured_argv == ["taint", "aws_instance.foo"]

    def test_untaint_invokes_untaint_address_args(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(tmp_path).untaint("aws_instance.foo")
        assert fake_terraform_bin.captured_argv == ["untaint", "aws_instance.foo"]

    def test_define_invokes_import_address_identifier_args(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(tmp_path).define("aws_instance.foo", "i-1234")
        assert fake_terraform_bin.captured_argv == [
            "import",
            "aws_instance.foo",
            "i-1234",
        ]

    def test_destroy_invokes_destroy_arg(
        self, tmp_path: Path, fake_terraform_bin: FakeTerraform
    ) -> None:
        Terraform(tmp_path).destroy()
        assert fake_terraform_bin.captured_argv == ["destroy"]
