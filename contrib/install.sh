#!/bin/sh
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
# Install terranova: tarball on macOS, deb/rpm package on Linux.
#
# Usage: install.sh [--version <x.y.z>] [--prefix <dir>] [--bin-dir <dir>]
#
# Note: release assets are not checksummed; provenance can be checked with
# `gh attestation verify <file> --repo techcode-io/terranova`.

set -eu

REPO="techcode-io/terranova"
VERSION="${TERRANOVA_VERSION:-}"
PREFIX=""
BIN_DIR=""

say() { printf '==> %s\n' "$*"; }
err() { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() {
  cat <<USAGE
Install terranova.

Options:
  --version <x.y.z>  Version to install (default: latest, or \$TERRANOVA_VERSION)
  --prefix <dir>     macOS only: install directory (default: /usr/local/opt/terranova
                     if writable via sudo/root, else ~/.local/opt/terranova)
  --bin-dir <dir>    macOS only: where to symlink the binary
                     (default: /usr/local/bin or ~/.local/bin)
  -h, --help         Show this help
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --version) [ $# -ge 2 ] || err "--version requires a value"; VERSION="$2"; shift 2 ;;
    --prefix) [ $# -ge 2 ] || err "--prefix requires a value"; PREFIX="$2"; shift 2 ;;
    --bin-dir) [ $# -ge 2 ] || err "--bin-dir requires a value"; BIN_DIR="$2"; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; err "unknown option: $1" ;;
  esac
done

if have curl; then
  download() { curl -fsSL -o "$2" "$1"; }
  resolve_latest() { curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest"; }
elif have wget; then
  download() { wget -q -O "$2" "$1"; }
  resolve_latest() { wget -q --server-response --spider "https://github.com/$REPO/releases/latest" 2>&1 | sed -n 's/^ *[Ll]ocation: //p' | tail -n 1; }
else
  err "curl or wget is required"
fi

# Run a command as root, using sudo when not already root.
as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif have sudo; then
    sudo "$@"
  else
    err "root privileges required and sudo not found"
  fi
}

case "$(uname -s)" in
  Darwin) OS="darwin" ;;
  Linux) OS="linux" ;;
  *) err "unsupported OS: $(uname -s)" ;;
esac

case "$(uname -m)" in
  arm64 | aarch64) ARCH="arm64"; RPM_ARCH="aarch64" ;;
  x86_64 | amd64) ARCH="amd64"; RPM_ARCH="x86_64" ;;
  *) err "unsupported architecture: $(uname -m)" ;;
esac

if [ -z "$VERSION" ]; then
  say "Resolving latest version"
  VERSION="$(resolve_latest)"
  VERSION="${VERSION##*/}"
  [ -n "$VERSION" ] && [ "$VERSION" != "latest" ] || err "could not determine latest version"
fi
VERSION="${VERSION#v}"
say "Installing terranova $VERSION ($OS/$ARCH)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT INT TERM
BASE_URL="https://github.com/$REPO/releases/download/$VERSION"

fetch() {
  say "Downloading $1"
  download "$BASE_URL/$1" "$TMP/$1" || err "download failed: $BASE_URL/$1"
}

install_macos() {
  ASSET="terranova-$VERSION-darwin-$ARCH.tar.gz"
  fetch "$ASSET"

  SUDO=""
  if [ -z "$PREFIX" ]; then
    if [ -w /usr/local/opt ] || { [ ! -e /usr/local/opt ] && [ -w /usr/local ]; }; then
      PREFIX="/usr/local/opt/terranova"
    else
      PREFIX="$HOME/.local/opt/terranova"
    fi
  fi
  if [ -z "$BIN_DIR" ]; then
    case "$PREFIX" in "$HOME"/*) BIN_DIR="$HOME/.local/bin" ;; *) BIN_DIR="/usr/local/bin" ;; esac
  fi

  # Fall back to sudo only if a target location is not writable by the user.
  mkdir -p "$PREFIX" "$BIN_DIR" 2>/dev/null || SUDO="as_root"
  [ -z "$SUDO" ] || { $SUDO mkdir -p "$PREFIX" "$BIN_DIR"; }
  [ -w "$PREFIX" ] && [ -w "$BIN_DIR" ] || SUDO="as_root"

  say "Extracting to $PREFIX"
  ${SUDO:+$SUDO} rm -rf "$PREFIX"
  ${SUDO:+$SUDO} mkdir -p "$PREFIX"
  ${SUDO:+$SUDO} tar -C "$PREFIX" -xzf "$TMP/$ASSET"
  ${SUDO:+$SUDO} chmod +x "$PREFIX/terranova"
  ${SUDO:+$SUDO} ln -sf "$PREFIX/terranova" "$BIN_DIR/terranova"
  BIN="$BIN_DIR/terranova"
}

install_linux() {
  if have dpkg; then
    ASSET="terranova_${VERSION}_${ARCH}.deb"
    fetch "$ASSET"
    say "Installing $ASSET"
    if have apt-get; then
      as_root apt-get install -y "$TMP/$ASSET"
    else
      as_root dpkg -i "$TMP/$ASSET"
    fi
  elif have rpm; then
    ASSET="terranova-${VERSION}-1.${RPM_ARCH}.rpm"
    fetch "$ASSET"
    say "Installing $ASSET"
    if have dnf; then
      as_root dnf install -y "$TMP/$ASSET"
    elif have zypper; then
      as_root zypper --non-interactive --no-gpg-checks install "$TMP/$ASSET"
    elif have yum; then
      as_root yum install -y "$TMP/$ASSET"
    else
      as_root rpm -U "$TMP/$ASSET"
    fi
  else
    err "no supported package manager (dpkg or rpm) found; see the README for manual installation"
  fi
  BIN="/usr/bin/terranova"
}

case "$OS" in
  darwin) install_macos ;;
  linux) install_linux ;;
esac

say "Installed: $("$BIN" --version 2>&1 | head -n 1 || true)"
case ":$PATH:" in
  *":$(dirname "$BIN"):"*) ;;
  *) say "warning: $(dirname "$BIN") is not on your PATH" ;;
esac
