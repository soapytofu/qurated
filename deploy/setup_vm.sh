#!/usr/bin/env bash
# Runs ON the VM (Debian 12) as root: installs Caddy + uv, unpacks Qurated, starts the service.
# Usage: sudo bash setup_vm.sh <hostname>   e.g. 34-12-56-78.sslip.io
set -euo pipefail
HOST="$1"

apt-get update -qq
apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl gnupg >/dev/null
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy >/dev/null
fi

id qurated >/dev/null 2>&1 || useradd --system --create-home --home-dir /opt/qurated --shell /usr/sbin/nologin qurated
mkdir -p /opt/qurated
tar -xzf /tmp/qurated.tgz -C /opt/qurated
chown -R qurated:qurated /opt/qurated

# uv (Python toolchain) for the service user, then install dependencies.
sudo -u qurated -H bash -c 'command -v ~/.local/bin/uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh'
sudo -u qurated -H bash -c 'cd /opt/qurated/app && ~/.local/bin/uv sync --frozen'

# 1 GB machines (e2-micro): add swap so Python + the course index never get OOM-killed.
if [ ! -f /swapfile ]; then fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab; fi

sed "s/{{HOST}}/${HOST}/" /opt/qurated/deploy/Caddyfile.tmpl > /etc/caddy/Caddyfile
cp /opt/qurated/deploy/qurated.service /etc/systemd/system/qurated.service
systemctl daemon-reload
systemctl enable --now caddy >/dev/null
systemctl reload caddy || systemctl restart caddy
if [ -f /opt/qurated/app/.env ]; then systemctl enable --now qurated; systemctl restart qurated; echo "Qurated started."; else echo "Waiting for app/.env (copy it, then: sudo systemctl enable --now qurated)"; fi
echo "Done: https://${HOST}"
