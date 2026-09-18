#!/usr/bin/env python3
"""Sandbox `initializeCommand`: stage the host's Claude Code and git state for `post_create.py`.

Runs on the host (see devcontainer.json), before the sandbox container exists. `~/.claude` inside
the sandbox is a separate, isolated volume, so the host's settings, onboarding state, plugins,
skills and auth wouldn't otherwise follow you in. Everything lands under `.devcontainer/.generated`
(gitignored); `poe claude:sandbox` deletes it again once the container is up, as it holds a live OAuth
token. Standard library only.
"""  # noqa: EXE001

from __future__ import annotations

import getpass
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Only for the annotation: nothing from the container-side script is loaded on the host.
    from post_create import HostManifest

OUT_DIR = Path(".devcontainer/.generated")
HOME = Path.home()

# Allowlist of `settings.json` keys to inherit. `permissions` in particular is never inherited: the
# sandbox skips permission prompts through the launch flag instead (see claude.sh). Anything else - `env` (may hold secrets), `hooks`,
# `apiKeyHelper`, host-specific blocks like the macOS Seatbelt `sandbox` - either doesn't make sense
# in the Linux container or would carry host trust into a session that runs without permission
# prompts. `statusLine` is handled separately below.
INHERITED_SETTINGS = (
    "enabledPlugins",
    "extraKnownMarketplaces",
    "model",
    "theme",
    "alwaysThinkingEnabled",
    "attribution",
    "includeCoAuthoredBy",
)

# Git settings to inherit: identity and harmless preferences. Everything else stays on the host -
# credential helpers, signing keys and `core.sshCommand`/`gpg.program` point at host binaries and
# secrets the sandbox deliberately doesn't have, `url.*.insteadOf` would rewrite HTTPS remotes to SSH
# (which its firewall blocks), and aliases, `core.fsmonitor` and `core.hooksPath` run arbitrary
# commands. So commits made in the sandbox are unsigned, and pushing needs its own credentials.
INHERITED_GIT_CONFIG = (
    "user.name",
    "user.email",
    "init.defaultBranch",
    "pull.rebase",
    "pull.ff",
    "push.default",
    "push.autoSetupRemote",
    "fetch.prune",
    "rebase.autoStash",
)

# `~/.claude.json`: only the onboarding/account state. Without hasCompletedOnboarding Claude Code
# runs its first-run flow (including login) even when a valid token is present.
INHERITED_STATE = ("hasCompletedOnboarding", "oauthAccount")


def load_or_empty(path: Path) -> dict[str, Any]:
    """Read a JSON object; any problem (missing, unreadable, invalid or not an object) gives `{}`."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def oh_my_posh_config(status_line: dict[str, Any]) -> Path | None:
    """Find the config file of an `oh-my-posh claude --config <file>` status line command.

    Remote (URL) configs are skipped: the sandbox can't fetch them.
    """
    try:
        # Windows paths use backslashes, which POSIX-style splitting would treat as escapes.
        argv = shlex.split(str(status_line.get("command", "")), posix=os.name != "nt")
    except ValueError:
        return None
    config = config_argument(argv)
    if not config or config.startswith(("http://", "https://")):
        return None
    path = Path(os.path.expandvars(config)).expanduser()
    return path if path.is_file() else None


def config_argument(argv: list[str]) -> str | None:
    """Return the value of the last `--config <file>`, `-c <file>` or `--config=<file>` flag."""
    config = None
    for arg, following in zip(argv, [*argv[1:], None]):
        if arg in ("--config", "-c") and following is not None:
            config = following
        elif arg.startswith("--config="):
            config = arg.split("=", 1)[1]
    # On Windows the split keeps the quotes around the value.
    return config.strip("\"'") if config else None


def git_config() -> dict[str, str]:
    """Read the inherited git settings as git resolves them for this repository.

    Run from the workspace, so per-repository overrides and `includeIf` blocks (a work email for
    some directories, say) apply exactly as they do on the host.
    """
    if shutil.which("git") is None:
        return {}
    config = {}
    for key in INHERITED_GIT_CONFIG:
        result = subprocess.run(
            ["git", "config", "--get", key], capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            config[key] = result.stdout.strip()
    return config


def plugins() -> tuple[list[str], list[str]]:
    """List the remote marketplaces and the user-scope plugins installed on the host.

    Plugin files can't be copied (their install paths are absolute host paths), so the sandbox
    reinstalls them with the claude CLI. Local-path marketplaces don't exist in the container.
    """
    plugins_dir = HOME / ".claude" / "plugins"
    return (
        remote_marketplaces(load_or_empty(plugins_dir / "known_marketplaces.json")),
        user_scope_plugins(load_or_empty(plugins_dir / "installed_plugins.json")),
    )


def remote_marketplaces(known: dict) -> list[str]:
    """Pick out what `claude plugin marketplace add` takes for each marketplace reachable remotely.

    That's `owner/repo` for a GitHub source and the URL for a plain git one; any other kind
    (a local path, say) is skipped.
    """
    locations = {"github": "repo", "git": "url"}
    sources = [entry.get("source", {}) for entry in known.values()]
    return [
        source[locations[source["source"]]]
        for source in sources
        if source.get("source") in locations
    ]


def user_scope_plugins(installed: dict) -> list[str]:
    """List the ids of the plugins installed at user scope (not per project)."""
    return [
        plugin_id
        for plugin_id, installs in installed.get("plugins", {}).items()
        if any(install.get("scope") == "user" for install in installs)
    ]


def host_credentials() -> str | None:
    """Read Claude Code's OAuth token from where the host keeps it.

    macOS keeps it in the Keychain; Linux and Windows use a plain `~/.claude/.credentials.json`,
    the same file the container uses. None if there's none, or if access is denied.
    """
    file = HOME / ".claude" / ".credentials.json"
    if shutil.which("security") is None:
        return file.read_text() if file.is_file() else None
    result = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-s",
            "Claude Code-credentials",
            "-a",
            getpass.getuser(),
            "-w",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout
    return file.read_text() if file.is_file() else None


def main() -> None:
    """Stage everything `post_create.py` needs under `.devcontainer/.generated`."""
    os.umask(0o077)  # the credentials/account files below hold live auth data
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    OUT_DIR.mkdir(parents=True)

    host_settings = load_or_empty(HOME / ".claude" / "settings.json")
    state = load_or_empty(HOME / ".claude.json")
    status_line = host_settings.get("statusLine")
    if not isinstance(status_line, dict):
        status_line = {}
    marketplaces, user_plugins = plugins()

    omp_config = oh_my_posh_config(status_line)
    if omp_config:
        (OUT_DIR / "ohmyposh").mkdir()
        shutil.copyfile(omp_config, OUT_DIR / "ohmyposh" / omp_config.name)

    manifest: HostManifest = {
        "settings": {
            k: host_settings[k] for k in INHERITED_SETTINGS if k in host_settings
        },
        # The host's status line options (padding etc.) minus the host-specific `command`.
        "status_line": {k: v for k, v in status_line.items() if k != "command"},
        "oh_my_posh_config": omp_config.name if omp_config else None,
        "claude_json": {k: state[k] for k in INHERITED_STATE if k in state},
        "git_config": git_config(),
        "marketplaces": marketplaces,
        "plugins": user_plugins,
    }
    (OUT_DIR / "host.json").write_text(json.dumps(manifest))

    credentials = host_credentials()
    if credentials:
        (OUT_DIR / "credentials.json").write_text(credentials)

    # User-level skills are plain files (no host paths), so they're copied as-is, symlinks resolved.
    skills = HOME / ".claude" / "skills"
    if skills.is_dir():
        shutil.copytree(skills, OUT_DIR / "skills")


if __name__ == "__main__":
    main()
