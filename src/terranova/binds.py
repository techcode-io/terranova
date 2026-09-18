#
# Copyright 2023-2025 Elasticsearch B.V.
# Copyright 2026-present Adrien Mannocci
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import json
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Final, cast, override

from terranova.exceptions import InvalidResourcesError
from terranova.parser import TfEvent, iter_events
from terranova.process import Bind, Command, CommandNotFound, EnvCmd, ErrorReturnCode
from terranova.utils import AppContext, int_or_default, str_or_none

_DIAGNOSTIC_RULE: Final[str] = "─" * 60
_MAX_FALLBACK_DIAGNOSTIC_LENGTH: Final[int] = 4000
# Glyph/style for each terraform `change.action` worth showing in a resource-level
# diff - matches the +/~/-/-+ convention from terraform's own plan output.
# "no-op" and "read" are deliberately absent: neither is a change to show.
_ACTION_GLYPHS: Final[dict[str, tuple[str, str]]] = {
    "create": ("+", "green"),
    "update": ("~", "yellow"),
    "delete": ("-", "red"),
    "replace": ("-/+", "red"),
}


@dataclass(frozen=True)
class ResourceChange:
    """One resource's planned action, as reported by a `planned_change` event."""

    address: str
    action: str

    def render(self) -> str:
        """Render a single resource-diff line, e.g. `  + aws_instance.foo`."""
        glyph, style = _ACTION_GLYPHS.get(self.action, ("?", "white"))
        return f"  [{style}]{glyph} {self.address}[/{style}]"


@dataclass(frozen=True)
class ChangeSummary:
    """Terranova's own summary of a `plan`/`apply` run - not terraform's `-json` schema."""

    to_add: int = 0
    to_change: int = 0
    to_destroy: int = 0
    resources: tuple[ResourceChange, ...] = ()
    diagnostics: tuple[str, ...] = ()

    @property
    def has_errors(self) -> bool:
        """True if any error diagnostic was observed."""
        return bool(self.diagnostics)

    def render(self, rel_path: str) -> str:
        """Render a compact, human-readable rendering of this summary."""
        if self.diagnostics:
            # Ruled header, so one project's failure is easy to spot and doesn't
            # run into the next one when several print back-to-back (parallel
            # execution) or the rel_path itself is long.
            lines = [
                _DIAGNOSTIC_RULE,
                f"[bold red]✗ {rel_path}[/bold red]",
                _DIAGNOSTIC_RULE,
            ]
            for message in self.diagnostics:
                lines.extend(f"  {line}" for line in message.splitlines() or [""])
            return "\n".join(lines)
        summary_line = (
            f"{rel_path}: Plan: {self.to_add} to add, {self.to_change} to change, "
            f"{self.to_destroy} to destroy."
        )
        lines = [summary_line]
        lines.extend(resource.render() for resource in self.resources)
        return "\n".join(lines)

    @staticmethod
    def parse(text: str) -> "ChangeSummary":
        """Interpret a captured `terraform plan/apply -json` stream as a `ChangeSummary`."""
        to_add = to_change = to_destroy = 0
        resources: list[ResourceChange] = []
        diagnostics: list[str] = []

        for event in iter_events(text):
            if event.type == "diagnostic" and event.level == "error":
                message = ChangeSummary._format_diagnostic(event)
                if message:
                    diagnostics.append(message)
            elif event.type == "change_summary":
                raw_changes = event.raw.get("changes")
                if isinstance(raw_changes, dict):
                    changes = cast("dict[str, object]", raw_changes)
                    to_add = int_or_default(changes.get("add"), to_add)
                    to_change = int_or_default(changes.get("change"), to_change)
                    to_destroy = int_or_default(changes.get("remove"), to_destroy)
            elif event.type == "planned_change":
                raw_change = event.raw.get("change")
                if not isinstance(raw_change, dict):
                    continue
                raw_change = cast("dict[str, object]", raw_change)
                action = raw_change.get("action")
                if action == "create":
                    to_add += 1
                elif action == "update":
                    to_change += 1
                elif action == "delete":
                    to_destroy += 1
                resource = ChangeSummary._format_resource_change(raw_change, action)
                if resource:
                    resources.append(resource)

        return ChangeSummary(
            to_add=to_add,
            to_change=to_change,
            to_destroy=to_destroy,
            resources=tuple(resources),
            diagnostics=tuple(diagnostics),
        )

    @staticmethod
    def parse_failure(text: str, exit_code: int) -> "ChangeSummary":
        """
        Summarize a failed `-json` capture, always producing at least one diagnostic.

        Terraform can fail without ever emitting a JSON `diagnostic` event - it may
        crash before `-json` mode takes effect, print a plain-text error to stderr,
        or die from something outside its own control (missing binary dependency,
        permission error, ...). `ChangeSummary.parse()` alone would silently drop all
        of that, leaving a failure with no explanation. Fall back to the raw captured
        output in that case, so a failure always carries some clue.
        """
        summary = ChangeSummary.parse(text)
        if summary.diagnostics:
            return summary

        raw = text.strip()
        if not raw:
            fallback = f"terraform exited with code {exit_code} and produced no output"
        elif len(raw) > _MAX_FALLBACK_DIAGNOSTIC_LENGTH:
            fallback = "…" + raw[-_MAX_FALLBACK_DIAGNOSTIC_LENGTH:]
        else:
            fallback = raw

        return ChangeSummary(
            to_add=summary.to_add,
            to_change=summary.to_change,
            to_destroy=summary.to_destroy,
            resources=summary.resources,
            diagnostics=(fallback,),
        )

    @staticmethod
    def _format_resource_change(
        raw_change: dict[str, object], action: object
    ) -> "ResourceChange | None":
        """
        Build a `ResourceChange` from one `planned_change` event's `change` object.

        `"no-op"` and `"read"` actions are skipped - neither is a change worth
        showing in a resource-level diff.
        """
        if not isinstance(action, str) or action in ("no-op", "read"):
            return None
        raw_resource = raw_change.get("resource")
        if not isinstance(raw_resource, dict):
            return None
        address = cast("dict[str, object]", raw_resource).get("addr")
        if not isinstance(address, str) or not address:
            return None
        return ResourceChange(address=address, action=action)

    @staticmethod
    def _format_diagnostic(event: TfEvent) -> str | None:
        """
        Render one terraform `diagnostic` event as a human-readable message.

        The top-level `@message` field is just the diagnostic's summary - the
        actual explanation (e.g. an external program's stderr output) lives in
        the nested `diagnostic.detail` field and is otherwise silently dropped.
        """
        raw_diagnostic = event.raw.get("diagnostic")
        summary_text = event.message
        detail_text: str | None = None
        if isinstance(raw_diagnostic, dict):
            raw_diagnostic = cast("dict[str, object]", raw_diagnostic)
            candidate_summary = raw_diagnostic.get("summary")
            if isinstance(candidate_summary, str) and candidate_summary:
                summary_text = candidate_summary
            candidate_detail = raw_diagnostic.get("detail")
            if isinstance(candidate_detail, str) and candidate_detail:
                detail_text = candidate_detail
        if not summary_text:
            return detail_text
        if detail_text and detail_text != summary_text:
            return f"{summary_text}\n{detail_text}"
        return summary_text


@dataclass(frozen=True)
class ValidationDiagnostic:
    """One diagnostic entry from a `validate` run - not terraform's `-json` schema."""

    severity: str
    summary: str
    detail: str | None = None

    @staticmethod
    def parse(raw: object) -> "ValidationDiagnostic | None":
        """Interpret one raw `diagnostics[]` entry, or `None` if it's not a usable one."""
        if not isinstance(raw, dict):
            return None
        raw = cast("dict[str, object]", raw)
        severity = raw.get("severity")
        summary = raw.get("summary")
        if not isinstance(severity, str) or not isinstance(summary, str):
            return None
        return ValidationDiagnostic(
            severity=severity,
            summary=summary,
            detail=str_or_none(raw.get("detail")),
        )


@dataclass(frozen=True)
class ValidationResult:
    """Terranova's own report of a `validate` run - not terraform's `-json` schema."""

    valid: bool
    error_count: int
    warning_count: int
    diagnostics: tuple[ValidationDiagnostic, ...] = ()

    @staticmethod
    def parse(text: str) -> "ValidationResult":
        """
        Interpret the single JSON object produced by `terraform validate -json`.

        Args:
            text: the full captured stdout of a `terraform validate -json` invocation.

        Returns:
            the structured validation result.

        Raises:
            ValueError: if `text` isn't valid JSON.
            TypeError: if `text` is valid JSON but not a JSON object.
        """
        payload = cast("object", json.loads(text))
        if not isinstance(payload, dict):
            raise TypeError("terraform validate -json did not return a JSON object")
        payload = cast("dict[str, object]", payload)

        diagnostics: list[ValidationDiagnostic] = []
        raw_diagnostics = payload.get("diagnostics")
        if isinstance(raw_diagnostics, list):
            for raw_diagnostic in cast("list[object]", raw_diagnostics):
                diagnostic = ValidationDiagnostic.parse(raw_diagnostic)
                if diagnostic is not None:
                    diagnostics.append(diagnostic)

        return ValidationResult(
            valid=bool(payload.get("valid", False)),
            error_count=int_or_default(payload.get("error_count"), 0),
            warning_count=int_or_default(payload.get("warning_count"), 0),
            diagnostics=tuple(diagnostics),
        )


class TerraformChangeError(ErrorReturnCode):
    """Raised like `ErrorReturnCode`, but also carries the parsed `-json` change summary."""

    def __init__(self, cmd: str, exit_code: int, summary: ChangeSummary) -> None:
        """Init terraform change error."""
        super().__init__(cmd=cmd, exit_code=exit_code)
        self.summary: ChangeSummary = summary


class Terraform(Bind):
    """Represents a bind to terraform command."""

    def __init__(
        self,
        ctx: AppContext,
        work_dir: Path,
        variables: dict[str, str] | None = None,
    ) -> None:
        """Init terraform bind."""
        self.__ctx = ctx
        self.__work_dir = work_dir
        self.__variables = variables

        try:
            super().__init__("terraform")
        except CommandNotFound as err:
            ctx.log.fatal("detect terraform binary", err)

        try:
            ctx.terraform_shared_plugin_cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as err:
            ctx.log.fatal("create terraform cache directory", err)

    @override
    def create(self, cmd_path: str | Path) -> Command:
        inherit_env_vars = (
            "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
            "CLOUDSDK_CORE_PROJECT",
            "CLOUDSDK_PROJECT",
            "GCLOUD_PROJECT",
            "GCP_PROJECT",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_GHA_CREDS_PATH",
            "HOME",
            "PATH",
        )

        # Predicate for allowed env vars
        def is_allowed_env_var(env_var: str) -> bool:
            return env_var in inherit_env_vars or env_var.startswith(
                # Inherit terraform env vars, inherit terranova env vars, implicit
                # credentials for s3 backend, forward asdf for shims support
                ("TF_", "TERRANOVA_", "AWS_", "ASDF_")
            )

        env = EnvCmd.inherit(lambda k, _: is_allowed_env_var(k))

        # Bind variables
        additional_env_vars: dict[str, str] = {}
        if self.__variables:
            for key, value in self.__variables.items():
                additional_env_vars[f"TF_VAR_{key}"] = value

        # Bind plugin cache dir
        additional_env_vars["TF_PLUGIN_CACHE_DIR"] = (
            self.__ctx.terraform_shared_plugin_cache_dir.absolute().as_posix()
        )

        # Enable debug
        if self.__ctx.verbose:
            additional_env_vars["TF_LOG"] = "DEBUG"

        return (
            Command(cmd_path)
            .env(env.add(additional_env_vars).build())
            .cwd(self.__work_dir)
        )

    def init(
        self,
        backend_config: dict[str, str] | None = None,
        migrate_state: bool = False,
        no_backend: bool = False,
        reconfigure: bool = False,
        upgrade: bool = False,
    ) -> None:
        """Prepare your working directory for other commands."""
        args = ["init"]
        if reconfigure:
            args.append("-reconfigure")
        if upgrade:
            args.append("-upgrade")
        if migrate_state:
            args.append("-migrate-state")
        if no_backend:
            args.append("-backend=false")
        if backend_config:
            for key, value in backend_config.items():
                args.append(f"-backend-config={key}={value}")
        self._cmd.args(*args).inherit().exec()

    def validate(self) -> ValidationResult:
        """
        Check whether the configuration is valid.

        Returns:
            the structured validation report.

        Raises:
            InvalidResourcesError: if terraform didn't produce a parseable report
                (e.g. it crashed before validation could run).
        """
        capture = StringIO()
        try:
            self._cmd.args("validate", "-json").inherit().stdout(capture).exec()
        except ErrorReturnCode:
            # Invalid configuration exits non-zero but still emits a parseable
            # JSON report on stdout, so don't treat this as fatal here.
            pass
        try:
            return ValidationResult.parse(capture.getvalue())
        except (ValueError, TypeError) as err:
            raise InvalidResourcesError(
                cause="terraform validate did not produce a parseable report.",
                resolution="Run `terraform validate` directly in the project directory to see the raw error.",
            ) from err

    def fmt(self) -> None:
        """Reformat your configuration in the standard style."""
        self._cmd.args("fmt").inherit().exec()

    def _render_summary(
        self, rel_path: str, summary: ChangeSummary, quiet: bool
    ) -> None:
        """
        Print a `plan`/`apply` summary, unless suppressed.

        Rendering lives here rather than in the CLI commands so every caller
        (sequential or parallel execution) gets the same output for free.
        Errors are always shown, even when `quiet` is set - `quiet` only
        suppresses the noise of a routine, successful run.
        """
        if not quiet or summary.has_errors:
            self.__ctx.console.print(summary.render(rel_path))

    def plan(
        self,
        input: bool,
        no_color: bool,
        parallelism: int | None,
        detailed_exitcode: bool,
        out: Path | None = None,
        rel_path: str = "",
        quiet: bool = False,
    ) -> ChangeSummary:
        """
        Show changes required by the current configuration.

        Returns:
            terranova's own structured summary of the plan.

        Raises:
            TerraformChangeError: like `ErrorReturnCode`, but carrying the parsed
                summary, when terraform exits non-zero.
        """
        args = ["plan", "-json"]
        args.append("-input=true" if input else "-input=false")
        if no_color:
            args.append("-no-color")
        if parallelism and parallelism != 10:  # Default value is 10
            args.append(f"-parallelism={parallelism}")
        if detailed_exitcode:
            args.append("-detailed-exitcode")
        if out:
            args.append(f"-out={out.as_posix()}")
        capture = StringIO()
        try:
            self._cmd.args(*args).stdout(capture).stderr(capture).exec()
        except ErrorReturnCode as err:
            summary = ChangeSummary.parse_failure(capture.getvalue(), err.exit_code)
            self._render_summary(rel_path, summary, quiet)
            raise TerraformChangeError(
                cmd=err.cmd,
                exit_code=err.exit_code,
                summary=summary,
            ) from err
        summary = ChangeSummary.parse(capture.getvalue())
        self._render_summary(rel_path, summary, quiet)
        return summary

    def apply(
        self,
        plan: str | None = None,
        auto_approve: bool = False,
        target: str | None = None,
        rel_path: str = "",
        quiet: bool = False,
    ) -> ChangeSummary:
        """
        Create or update infrastructure.

        Returns:
            terranova's own structured summary of the apply.

        Raises:
            TerraformChangeError: like `ErrorReturnCode`, but carrying the parsed
                summary, when terraform exits non-zero.
        """
        # Neither a saved plan nor `-auto-approve` means terraform needs to
        # prompt for interactive approval - `-json` disables that prompt
        # outright, so it's only added once we know we don't need one. Safe
        # only when a single `apply` runs at a time with a real terminal
        # attached - callers must never take this path from parallel execution.
        interactive = not plan and not auto_approve
        args = ["apply"] if interactive else ["apply", "-json"]
        if plan:
            args.append(plan)
        if auto_approve:
            args.append("-auto-approve")
        if target:
            args.append(f"-target={target}")

        if interactive:
            self._cmd.args(*args).inherit().exec()
            return ChangeSummary()

        capture = StringIO()
        try:
            self._cmd.args(*args).stdout(capture).stderr(capture).exec()
        except ErrorReturnCode as err:
            summary = ChangeSummary.parse_failure(capture.getvalue(), err.exit_code)
            self._render_summary(rel_path, summary, quiet)
            raise TerraformChangeError(
                cmd=err.cmd,
                exit_code=err.exit_code,
                summary=summary,
            ) from err
        summary = ChangeSummary.parse(capture.getvalue())
        self._render_summary(rel_path, summary, quiet)
        return summary

    def graph(self) -> None:
        """Generate a Graphviz graph of the steps in an operation."""
        self._cmd.args("graph").inherit().exec()

    def taint(self, address: str) -> None:
        """Mark a resource as not fully functional."""
        self._cmd.args("taint", address).inherit().exec()

    def untaint(self, address: str) -> None:
        """Remove the 'tainted' state from a resource instance."""
        self._cmd.args("untaint", address).inherit().exec()

    def output(self, name: str) -> str:
        """Show output values from your root module."""
        capture = StringIO()
        self._cmd.args("output", "-raw", name).inherit().stdout(capture).exec()
        return capture.getvalue()

    def define(self, address: str, identifier: str) -> None:
        """Associate existing infrastructure with a Terraform resource."""
        self._cmd.args("import", address, identifier).inherit().exec()

    def destroy(self) -> None:
        """Destroy previously-created infrastructure."""
        self._cmd.args("destroy").inherit().exec()


class Git(Bind):
    """Represents a bind to git command."""

    def __init__(self, ctx: AppContext, work_dir: Path) -> None:
        """Init git bind."""
        try:
            super().__init__("git")
        except CommandNotFound as err:
            ctx.log.fatal("detect git binary", err)
        self.cwd(work_dir)

    def repo_root(self) -> str:
        """Show the absolute path to the top-level of the working tree."""
        capture = StringIO()
        self._cmd.args("rev-parse", "--show-toplevel").stdout(capture).stderr(
            capture
        ).exec()
        return capture.getvalue().strip()

    def changed_files(self) -> list[str]:
        """
        List files changed relative to HEAD (staged + unstaged) plus untracked files.

        Returns:
            paths relative to `cwd()` - callers should set `cwd()` to the repo
            root first (via `repo_root()`) so output is unambiguous.
        """
        capture = StringIO()
        self._cmd.args("diff", "--name-only", "HEAD").stdout(capture).stderr(
            capture
        ).exec()
        tracked = [line for line in capture.getvalue().splitlines() if line]

        capture = StringIO()
        self._cmd.args(
            "ls-files", "--others", "--exclude-standard", "--full-name"
        ).stdout(capture).stderr(capture).exec()
        untracked = [line for line in capture.getvalue().splitlines() if line]

        return tracked + untracked

    def init(self) -> None:
        """Create an empty git repository - only used to build fixtures in tests."""
        self._cmd.args("init", "-q").exec()

    def add(self, *paths: str) -> None:
        """Stage `paths` (or everything, if none given) - only used in tests."""
        self._cmd.args("add", *(paths or ("-A",))).exec()

    def commit(self, message: str, *, author: tuple[str, str] | None = None) -> None:
        """
        Commit staged changes - only used to build fixtures in tests.

        Args:
            message: the commit message.
            author: an optional `(name, email)` override, so tests don't
                depend on the host's global git identity being configured.
        """
        args: list[str] = []
        if author:
            name, email = author
            args += ["-c", f"user.name={name}", "-c", f"user.email={email}"]
        args += ["commit", "-q", "-m", message]
        self._cmd.args(*args).exec()
