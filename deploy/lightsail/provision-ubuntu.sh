#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this once with sudo on an Ubuntu 24.04 server." >&2
  exit 1
fi

domain="${1:-}"
email="${2:-}"
deploy_user="${3:-${SUDO_USER:-}}"

if [[ -z "${domain}" || -z "${email}" || -z "${deploy_user}" ]]; then
  echo "Usage: sudo bash provision-ubuntu.sh converter.example.com owner@example.com [deploy-user]" >&2
  exit 2
fi
if [[ "${deploy_user}" == "root" ]] || ! id "${deploy_user}" >/dev/null 2>&1; then
  echo "Deploy user must be an existing non-root account with SSH and sudo access." >&2
  exit 2
fi
if [[ ! "${domain}" =~ ^[A-Za-z0-9.-]+$ ]] || [[ "${domain}" != *.* ]]; then
  echo "DOMAIN must be a DNS hostname such as converter.example.com." >&2
  exit 2
fi
if [[ ! "${email}" =~ ^[A-Za-z0-9._+@-]+$ ]] || [[ "${email}" != *@*.* ]]; then
  echo "ACME email contains unsupported characters." >&2
  exit 2
fi

apt-get update
apt-get install -y ca-certificates curl git openssl unattended-upgrades
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
  # This secret authorizes shared PWA links. It is intentionally never printed.
  app_access_token="$(openssl rand -hex 32)"
  install -m 0600 -o "${deploy_user}" -g "${deploy_user}" /dev/null "${environment_file}"
  {
    echo "DOMAIN=${domain}"
    echo "ACME_EMAIL=${email}"
    echo "DATA_DIR=/opt/nutrilite-converter/shared/data"
    echo "APP_VERSION=latest"
    echo "OPENAI_API_KEY=REPLACE_WITH_OPENAI_API_KEY"
    echo "OPENAI_MODEL=gpt-5.4-mini"
    echo "APP_ACCESS_TOKEN=${app_access_token}"
    echo "ANALYSIS_RATE_LIMIT_PER_HOUR=20"
    echo "MAX_REQUEST_BYTES=8500000"
    echo "MAX_IMAGE_BYTES=6000000"
    echo "CADDY_MAX_REQUEST_BODY_SIZE=8500000"
  } > "${environment_file}"
else
  # Upgrade an environment created by an earlier release without exposing or
  # replacing any existing credential.
  chmod 0600 "${environment_file}"
  chown "${deploy_user}:${deploy_user}" "${environment_file}"
  if ! grep -q '^OPENAI_API_KEY=' "${environment_file}"; then
    echo "OPENAI_API_KEY=REPLACE_WITH_OPENAI_API_KEY" >> "${environment_file}"
  fi
  if ! grep -q '^OPENAI_MODEL=' "${environment_file}"; then
    echo "OPENAI_MODEL=gpt-5.4-mini" >> "${environment_file}"
  fi
  existing_access_token="$(sed -n 's/^APP_ACCESS_TOKEN=//p' "${environment_file}" | tail -n 1)"
  if [[ -z "${existing_access_token}" || "${existing_access_token}" == REPLACE_* || ${#existing_access_token} -lt 32 ]]; then
    generated_access_token="$(openssl rand -hex 32)"
    if grep -q '^APP_ACCESS_TOKEN=' "${environment_file}"; then
      sed -i "s/^APP_ACCESS_TOKEN=.*/APP_ACCESS_TOKEN=${generated_access_token}/" "${environment_file}"
    else
      echo "APP_ACCESS_TOKEN=${generated_access_token}" >> "${environment_file}"
    fi
  fi
  if ! grep -q '^ANALYSIS_RATE_LIMIT_PER_HOUR=' "${environment_file}"; then
    echo "ANALYSIS_RATE_LIMIT_PER_HOUR=20" >> "${environment_file}"
  fi
  if ! grep -q '^MAX_REQUEST_BYTES=' "${environment_file}"; then
    echo "MAX_REQUEST_BYTES=8500000" >> "${environment_file}"
  fi
  if ! grep -q '^MAX_IMAGE_BYTES=' "${environment_file}"; then
    echo "MAX_IMAGE_BYTES=6000000" >> "${environment_file}"
  fi
  if ! grep -q '^CADDY_MAX_REQUEST_BODY_SIZE=' "${environment_file}"; then
    echo "CADDY_MAX_REQUEST_BODY_SIZE=8500000" >> "${environment_file}"
  fi
fi

echo "Provisioning complete. Log out and back in so Docker group membership applies."
echo "Open Lightsail firewall ports 22, 80, and 443 and point ${domain} to the static IP."
echo "Before deployment, replace OPENAI_API_KEY in ${environment_file}."
echo "The generated app access token is protected in that file and was not printed."
