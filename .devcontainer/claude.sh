#!/usr/bin/env bash
# Runs Claude Code in the sandbox with permission prompts skipped (the container and its firewall
# are the boundary). Given an IDE name as $1, also wires Claude to the host IDE: the host side
# (scripts/tasks/sandbox.py, started by `poe claude:sandbox`) relays to the IDE and adds the real auth token,
# so a placeholder is all that's written here. Selection/diagnostics context then flows in through
# the regular `--ide` integration.
set -euo pipefail

# Keep in sync with IDE_RELAY_PORT in scripts/tasks/sandbox.py and the rule in init-sandbox.sh.
relay_port=41337

# The host passes its terminal's size (COLUMNS/LINES) and TERM. Apply the size to this pty, which
# doesn't reliably start out with it, and fall back to a universal TERM if the container's terminfo
# doesn't know the host's. COLUMNS/LINES only seed the pty: they're dropped afterwards because they
# are a snapshot from launch, and programs that prefer them over the pty size would ignore resizes.
if [ -t 0 ]; then
  [ -z "${LINES:-}" ] || [ -z "${COLUMNS:-}" ] || stty rows "$LINES" cols "$COLUMNS" 2>/dev/null || true
  unset LINES COLUMNS
  infocmp "${TERM:-}" >/dev/null 2>&1 || export TERM=xterm-256color
fi

# The terminal title is set by `poe claude:sandbox` on the host; without this Claude Code overwrites it.
export CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1

# Render in Claude Code's fullscreen mode from the start. Unlike the saved `tui` setting, this is
# still honoured after a failed fullscreen start, which would otherwise drop it back to the classic
# renderer for good on this machine.
export CLAUDE_CODE_NO_FLICKER=1

# Leave mouse selection to the terminal. In fullscreen mode Claude Code captures the mouse and does
# the copy itself: with a native tool (pbcopy, wl-copy, xclip, xsel - none exist in this container)
# or, only over SSH, with OSC 52. Neither works here, so copying by mouse silently fails. With the
# capture off, dragging selects natively and Cmd+C copies; PgUp/PgDn/Ctrl+Home/Ctrl+End still scroll,
# but the wheel, clicks and hover inside Claude Code are lost. To keep them instead, delete this line
# and hold Shift while dragging (Option in iTerm2) to select natively.
export CLAUDE_CODE_DISABLE_MOUSE=1

args=(--dangerously-skip-permissions)

if [ -n "${1:-}" ]; then
  lock_dir="$HOME/.claude/ide"
  lock="$lock_dir/$relay_port.lock"
  mkdir -p "$lock_dir"
  # pid is this script's, which stays alive for as long as Claude runs (it is not exec'd).
  jq -n --argjson pid "$$" --arg workspace "$PWD" --arg ide "$1" \
    '{pid: $pid, workspaceFolders: [$workspace], ideName: $ide, transport: "ws", authToken: "injected-by-host-relay"}' \
    >"$lock"

  # The host is host.containers.internal under podman and host.docker.internal under Docker.
  host=host.containers.internal
  getent hosts "$host" >/dev/null || host=host.docker.internal

  # Claude connects to the IDE at 127.0.0.1:$relay_port, the port in the lock file above. This
  # forwards it to the relay on the host (fork: one child per connection, reuseaddr: a quick
  # restart can rebind the port). Loopback only, so nothing else in the container's network can
  # use it.
  socat "TCP-LISTEN:$relay_port,bind=127.0.0.1,fork,reuseaddr" "TCP:$host:$relay_port" &
  socat_pid=$!
  # Claude runs in the foreground below, so clean up when this script exits, however it does: stop
  # the forwarder and remove the lock file, which would otherwise point at a dead port.
  trap 'kill "$socat_pid" 2>/dev/null || true; rm -f "$lock"' EXIT

  args+=(--ide)
fi

claude "${args[@]}"
