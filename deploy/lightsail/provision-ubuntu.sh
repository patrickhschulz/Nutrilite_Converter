#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this once with sudo on an Ubuntu 24.04 Lightsail instance." >&2
  exit 1
fi

domain="${1:-}"
email="${2:-}"
deploy_user="${SUDO_USER:-ubuntu}"

if [[ -z "${domain}" || -z "${email}" ]]; then
  echo "Usage: sudo bash provision-ubuntu.sh converter.example.com owner@example.com" >&2
  exit 2
fi

apt-get update
apt-get install -y ca-certificates curl git unattended-upgrades
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

architecture="$(dpkg --print-architecture)"
codename="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
echo "deb [arch=${architecture} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
usermod -aG docker "${deploy_user}"

# GitHub-hosted runners do not have a fixed egress address. Keep SSH
# key-only if port 22 must be reachable from them.
ssh_hardening=/etc/ssh/sshd_config.d/00-nutrilite-converter.conf
{
  echo "PasswordAuthentication no"
  echo "KbdInteractiveAuthentication no"
  echo "PermitRootLogin no"
  echo "PubkeyAuthentication yes"
} > "${ssh_hardening}"
chmod 0644 "${ssh_hardening}"
sshd -t
if ! sshd -T | grep -q '^passwordauthentication no$'; then
  echo "SSH hardening check failed; password authentication is still enabled." >&2
  exit 1
fi
systemctl reload ssh

auto_updates=/etc/apt/apt.conf.d/20auto-upgrades
{
  echo 'APT::Periodic::Update-Package-Lists "1";'
  echo 'APT::Periodic::Unattended-Upgrade "1";'
} > "${auto_updates}"
systemctl enable --now apt-daily.timer apt-daily-upgrade.timer

install -d -o "${deploy_user}" -g "${deploy_user}" /opt/nutrilite-converter
install -d -o "${deploy_user}" -g "${deploy_user}" /opt/nutrilite-converter/releases
install -d -o 10001 -g 10001 /opt/nutrilite-converter/shared/data
install -d -o 10001 -g 10001 /opt/nutrilite-converter/shared/data/backups

environment_file=/opt/nutrilite-converter/.env
if [[ ! -e "${environment_file}" ]]; then
  install -m 0600 -o "${deploy_user}" -g "${deploy_user}" /dev/null "${environment_file}"
  {
    echo "DOMAIN=${domain}"
    echo "ACME_EMAIL=${email}"
    echo "DATA_DIR=/opt/nutrilite-converter/shared/data"
    echo "APP_VERSION=latest"
  } > "${environment_file}"
fi

echo "Provisioning complete. Log out and back in so Docker group membership applies."
echo "Open Lightsail firewall ports 22, 80, and 443 and point ${domain} to the static IP."
