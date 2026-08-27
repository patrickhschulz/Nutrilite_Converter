#!/usr/bin/env bash
set -euo pipefail

commit_sha="${1:-}"
archive="${2:-}"
deploy_root=/opt/nutrilite-converter

if [[ ! "${commit_sha}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Invalid commit SHA" >&2
  exit 2
fi
if [[ "${archive}" != "/tmp/nutrilite-${commit_sha}.tgz" || ! -f "${archive}" ]]; then
  echo "Invalid or missing deployment archive" >&2
  exit 2
fi

release_directory="${deploy_root}/releases/${commit_sha}"
mkdir -p "${release_directory}"
tar -xzf "${archive}" -C "${release_directory}"
rm -f -- "${archive}"
bash "${release_directory}/deploy/lightsail/deploy.sh" "${release_directory}"
