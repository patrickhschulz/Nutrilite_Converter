#!/usr/bin/env bash
set -euo pipefail

release_directory="${1:-}"
deploy_root=/opt/nutrilite-converter
environment_file="${deploy_root}/.env"

if [[ -z "${release_directory}" || ! -d "${release_directory}" ]]; then
  echo "Usage: deploy.sh /opt/nutrilite-converter/releases/COMMIT_SHA" >&2
  exit 2
fi
if [[ ! -f "${environment_file}" ]]; then
  echo "Missing ${environment_file}; run provision-ubuntu.sh first." >&2
  exit 1
fi

set -a
source "${environment_file}"
set +a
if [[ -z "${DOMAIN:-}" || "${DOMAIN}" == "converter.example.com" ]]; then
  echo "Set a real DOMAIN in ${environment_file}." >&2
  exit 1
fi

ln -sfn "${release_directory}" "${deploy_root}/current"
compose_file="${deploy_root}/current/deploy/lightsail/compose.production.yaml"

docker compose --env-file "${environment_file}" -f "${compose_file}" config --quiet
docker compose --env-file "${environment_file}" -f "${compose_file}" up \
  --detach --build --force-recreate --remove-orphans

sudo install -m 0644 \
  "${deploy_root}/current/deploy/lightsail/systemd/nutrilite-refresh.service" \
  /etc/systemd/system/nutrilite-refresh.service
sudo install -m 0644 \
  "${deploy_root}/current/deploy/lightsail/systemd/nutrilite-refresh.timer" \
  /etc/systemd/system/nutrilite-refresh.timer
sudo install -m 0644 \
  "${deploy_root}/current/deploy/lightsail/systemd/nutrilite-backup.service" \
  /etc/systemd/system/nutrilite-backup.service
sudo install -m 0644 \
  "${deploy_root}/current/deploy/lightsail/systemd/nutrilite-backup.timer" \
  /etc/systemd/system/nutrilite-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now nutrilite-refresh.timer nutrilite-backup.timer

for attempt in {1..20}; do
  if docker compose --env-file "${environment_file}" -f "${compose_file}" \
      exec -T app python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"; then
    echo "Deployment is healthy at https://${DOMAIN}"
    exit 0
  fi
  sleep 3
done

docker compose --env-file "${environment_file}" -f "${compose_file}" logs --tail=100
echo "Deployment failed its health check." >&2
exit 1
