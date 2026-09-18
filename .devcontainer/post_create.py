#!/usr/bin/env python3
"""Sandbox `postCreateCommand`: apply the host state staged by `initialize.py`, then set up the repo.

Run through `uv run --no-project` (see devcontainer.json), so it uses the image's uv-managed
Python. Standard library only.
"""  # noqa: EXE001

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, TypedDict

WORKSPACE: Final[Path] = Path(__file__).resolve().parent.parent
GENERATED: Final[Path] = WORKSPACE / ".devcontainer" / ".generated"
HOME: Final[Path] = Path.home()
CLAUDE_DIR: Final[Path] = HOME / ".claude"
INIT_SANDBOX: Final[str] = "/usr/local/bin/init-sandbox.sh"
PLUGIN_TIMEOUT: Final[int] = 180

# The sandbox runs without permission prompts (the container and its firewall are the boundary):
# claude.sh launches Claude with --dangerously-skip-permissions, and this skips the one-time "are
# you sure" dialog that flag would otherwise show. Applied last so nothing inherited can override it.
SANDBOX_SETTINGS: Final[Mapping[str, Any]] = {
    "skipDangerousModePermissionPrompt": True,
}


class HostManifest(TypedDict):
    """The host's Claude Code and git state that the sandbox inherits.

    Written to `.devcontainer/.generated/host.json` on the host by `initialize.py`, read here.
    The annotation is static only: the JSON is not validated at runtime.
    """

    # Allowlisted keys of the host's settings.json.
    settings: dict[str, Any]
    # The host's statusLine options minus the host-specific `command`.
    status_line: dict[str, Any]
    # File name (under .generated/ohmyposh/) of the oh-my-posh config the host's status line uses.
    oh_my_posh_config: str | None
    # Onboarding/account state of the host's ~/.claude.json.
    claude_json: dict[str, Any]
    # Allowlisted git settings (identity, preferences), as git resolves them for this repository.
    git_config: dict[str, str]
    # Remote marketplaces and user-scope plugins to reinstall in the container.
    marketplaces: list[str]
    plugins: list[str]


def load_or_empty(path: Path) -> dict[str, Any]:
    """Read a JSON object; any problem (missing, unreadable, invalid or not an object) gives `{}`."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_manifest() -> HostManifest:
    """Read the manifest `initialize.py` staged; an empty one if there is none (e.g. VS Code)."""
    data = load_or_empty(GENERATED / "host.json")
    return HostManifest(
        settings=data.get("settings", {}),
        status_line=data.get("status_line", {}),
        oh_my_posh_config=data.get("oh_my_posh_config"),
        claude_json=data.get("claude_json", {}),
        git_config=data.get("git_config", {}),
        marketplaces=data.get("marketplaces", []),
        plugins=data.get("plugins", []),
    )


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Merge `override` into `base` recursively; on any other conflict `override` wins."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def warn(message: str) -> None:
    """Report a non-fatal problem."""
    print(f"WARNING: {message}", file=sys.stderr)


def install_status_line(host: HostManifest) -> dict[str, Any]:
    """Build the `statusLine` setting: oh-my-posh (baked into the image), with the host's config.

    The host's command can't be reused as is (host paths and binaries), so it's rebuilt; the
    host's other status line options (padding etc.) are kept.
    """
    command = "oh-my-posh claude"
    config = host["oh_my_posh_config"]
    if config:
        target = HOME / ".config" / "ohmyposh"
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(GENERATED / "ohmyposh" / config, target / config)
        command = f"oh-my-posh claude --config ~/.config/ohmyposh/{config}"
    return {"type": "command", **host["status_line"], "command": command}


def install_settings(host: HostManifest) -> None:
    """Write `~/.claude/settings.json` and `~/.claude.json` from the staged host state."""
    # Built from scratch, not merged into what's there: the ~/.claude volume outlives the
    # container, and earlier versions of this setup merged the host's whole settings.json
    # (permissions, hooks, env...) into it - a merge never removes keys, so they'd stay for good.
    settings = deep_merge(
        host["settings"],
        {"statusLine": install_status_line(host)},
    )
    settings = deep_merge(settings, SANDBOX_SETTINGS)
    (CLAUDE_DIR / "settings.json").write_text(json.dumps(settings, indent=2))
    (CLAUDE_DIR / "settings.local.json").unlink(missing_ok=True)

    # ~/.claude.json isn't persisted, but merge rather than overwrite in case the CLI wrote to it.
    state = deep_merge(load_or_empty(HOME / ".claude.json"), host["claude_json"])
    (HOME / ".claude.json").write_text(json.dumps(state, indent=2))

    # The host keychain is the source of truth for auth (macOS hosts only - see initialize.py), so
    # it replaces whatever token the container has.
    credentials = GENERATED / "credentials.json"
    if credentials.is_file():
        target = CLAUDE_DIR / ".credentials.json"
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(credentials.read_text())
        target.chmod(0o600)


def install_git_config(host: HostManifest) -> None:
    """Set the host's git identity and preferences as the container's global git config.

    Also trusts the workspace: depending on the container engine and host, the bind-mounted files
    can show up owned by another user, in which case git refuses to work in the repository
    ("dubious ownership") without this.
    """
    settings = {**host["git_config"], "safe.directory": str(WORKSPACE)}
    for key, value in settings.items():
        try:
            subprocess.run(["git", "config", "--global", key, value], check=True)
        except (OSError, subprocess.SubprocessError) as err:
            warn(f"`git config --global {key}` failed: {err}")


def install_skills() -> None:
    """Copy the host's user-level skills."""
    skills = GENERATED / "skills"
    if skills.is_dir():
        shutil.copytree(skills, CLAUDE_DIR / "skills", dirs_exist_ok=True)


def install_plugins(host: HostManifest) -> None:
    """Reinstall the host's user-scope plugins from their marketplaces, behind the firewall.

    Their install paths are absolute host paths, so they can't be copied; GitHub is allowlisted.
    Best-effort and time-boxed: a failed or hanging plugin shouldn't fail or stall creation.
    """
    commands = [
        ["claude", "plugin", "marketplace", "add", source]
        for source in host["marketplaces"]
    ] + [
        ["claude", "plugin", "install", plugin, "--scope", "user"]
        for plugin in host["plugins"]
    ]
    for command in commands:
        try:
            subprocess.run(command, check=True, timeout=PLUGIN_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as err:
            warn(f"`{' '.join(command)}` failed: {err}")


def main() -> None:
    """Set up the sandbox once, when the container is created."""
    # Fix volume ownership and apply the egress firewall before anything else runs, so dependency
    # installation below already happens behind it. This is the only thing vscode may sudo.
    subprocess.run(["sudo", INIT_SANDBOX], check=True)

    host = load_manifest()
    install_settings(host)
    install_git_config(host)
    install_skills()
    install_plugins(host)

    # `poe env:configure` (pre-commit install) is deliberately not run: it writes .git/hooks, which
    # run on the host at commit time and are mounted read-only here (see devcontainer.json).
    # Install them from the host.
    subprocess.run(["uv", "sync"], cwd=WORKSPACE, check=True)


if __name__ == "__main__":
    main()
