#!/bin/bash
# Root-only startup tasks for the sandbox. Baked into the image as
# /usr/local/bin/init-sandbox.sh and run via sudo - the only command the vscode user
# may sudo (see the Containerfile). Runs on every container start.
#   1. fixes ownership of the named volumes mounted under the vscode user's home
#   2. applies the egress firewall (IPv4 allowlist, IPv6 dropped)
#   3. verifies the vscode user has no other way to become root
set -euo pipefail
IFS=$'\n\t'

# Named volumes mounted under the vscode user's home are created root-owned by podman/docker on
# first use - along with any parent directories podman has to create to reach the mount point
# (e.g. ~/.cache itself, not just ~/.cache/uv).
chown -R vscode:vscode /home/vscode/.cache /home/vscode/.claude /commandhistory

echo "Applying egress firewall rules..."

iptables -F
iptables -X
iptables -t nat -F
iptables -t nat -X
iptables -t mangle -F
iptables -t mangle -X
ipset destroy allowed-domains 2>/dev/null || true

# Fail closed: everything is denied from here on, so any early exit below leaves the container
# locked down rather than open.
iptables -P INPUT DROP
iptables -P FORWARD DROP
iptables -P OUTPUT DROP

iptables -A OUTPUT -o lo -j ACCEPT
iptables -A INPUT -i lo -j ACCEPT

# Replies to our own outbound traffic. This must be in place before any lookup below: this script
# runs more than once (post-create, then every start), and the DROP policies from the previous run
# survive the flush above, so without it DNS replies never arrive and the allowlist is built empty.
iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# DNS only to the resolvers this container is configured with, not to arbitrary servers - a
# direct query to an attacker-controlled nameserver is the simplest exfiltration channel. (Queries
# forwarded by the configured resolver can't be blocked at this layer.)
RESOLVERS="$(awk '/^nameserver/ && $2 ~ /^[0-9.]+$/ {print $2}' /etc/resolv.conf)"
if [ -z "$RESOLVERS" ]; then
  echo "ERROR: no IPv4 nameserver found in /etc/resolv.conf" >&2
  exit 1
fi
while read -r resolver; do
  iptables -A OUTPUT -p udp --dport 53 -d "$resolver" -j ACCEPT
  iptables -A OUTPUT -p tcp --dport 53 -d "$resolver" -j ACCEPT
done <<< "$RESOLVERS"

ipset create allowed-domains hash:net

# Package registries, VCS, and Anthropic services needed for day-to-day work in this repo
# (uv sync, git/gh, Claude Code itself). Terraform isn't installed: the tests use a fake one.
# Every host here is a potential exfiltration channel, so don't add hosts that accept anonymous
# writes (e.g. error trackers). Add cloud-provider API hosts here too if e2e tests need to reach
# real infrastructure.
DOMAINS=(
  "registry.npmjs.org"
  "pypi.org"
  "files.pythonhosted.org"
  "astral.sh"
  "github.com"
  "api.github.com"
  "raw.githubusercontent.com"
  "objects.githubusercontent.com"
  "codeload.github.com"
  "cli.github.com"
  "api.anthropic.com"
)

for domain in "${DOMAINS[@]}"; do
  echo "Resolving ${domain}..."
  ips="$(dig +short A "$domain" | grep -E '^[0-9.]+$' || true)"
  if [ -z "$ips" ]; then
    echo "WARNING: failed to resolve ${domain}, skipping" >&2
    continue
  fi
  while read -r ip; do
    ipset add allowed-domains "$ip" 2>/dev/null || true
  done <<< "$ips"
done

# The host IDE relay (scripts/tasks/sandbox.py): one port on the host, nothing else of it. Nothing
# listens there unless `poe claude:sandbox` started the relay. Keep the port in sync with claude.sh.
IDE_RELAY_PORT=41337
# The host is host.containers.internal under podman and host.docker.internal under Docker.
HOST_IP="$(getent ahostsv4 host.containers.internal | awk 'NR==1 {print $1}' || true)"
[ -n "$HOST_IP" ] || HOST_IP="$(getent ahostsv4 host.docker.internal | awk 'NR==1 {print $1}' || true)"
if [ -n "$HOST_IP" ]; then
  iptables -A OUTPUT -p tcp -d "$HOST_IP" --dport "$IDE_RELAY_PORT" -j ACCEPT
else
  echo "WARNING: the host isn't resolvable from the container, IDE integration won't work" >&2
fi

if [ -z "$(ipset list allowed-domains | sed -n '/^Members:/,$p' | tail -n +2)" ]; then
  echo "ERROR: no allowlisted domain could be resolved, check DNS (resolvers: ${RESOLVERS//$'\n'/ })" >&2
  exit 1
fi
iptables -A OUTPUT -m set --match-set allowed-domains dst -j ACCEPT

# Refuse everything else outright instead of silently dropping it: a blocked connection then
# fails immediately with "connection refused" rather than hanging until it times out (which is
# what tools that try SSH before HTTPS, or a host missing from the allowlist, would otherwise do).
# The DROP policies set at the top stay as a backstop.
iptables -A OUTPUT -p tcp -j REJECT --reject-with tcp-reset
iptables -A OUTPUT -j REJECT --reject-with icmp-admin-prohibited

# The allowlist is IPv4-only, so IPv6 must not be a way around it. devcontainer.json also
# disables it via sysctl; this is the second layer.
if ip6tables -F 2>/dev/null; then
  ip6tables -X
  ip6tables -A OUTPUT -o lo -j ACCEPT
  ip6tables -A INPUT -i lo -j ACCEPT
  ip6tables -P INPUT DROP
  ip6tables -P FORWARD DROP
  ip6tables -P OUTPUT DROP
elif [ -n "$(ip -6 addr show scope global 2>/dev/null)" ]; then
  echo "ERROR: container has a global IPv6 address and ip6tables is unavailable" >&2
  exit 1
fi

echo "Firewall rules applied. Verifying..."
if curl -s --max-time 5 https://example.com >/dev/null 2>&1; then
  echo "ERROR: firewall verification failed - a non-allowlisted host was reachable" >&2
  exit 1
fi
if ! curl -s --max-time 5 https://api.github.com >/dev/null 2>&1; then
  echo "WARNING: could not reach api.github.com - check the allowlist" >&2
fi
echo "Firewall verification passed."

# The firewall is only a boundary if vscode can't remove it.
if runuser -u vscode -- sudo -n true 2>/dev/null; then
  echo "ERROR: vscode can run arbitrary commands as root, the firewall could be disabled" >&2
  exit 1
fi
echo "Sudo restrictions verified."
