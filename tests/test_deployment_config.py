from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentConfigurationTests(unittest.TestCase):
    def test_container_includes_pwa_and_server_only_configuration(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        compose = (ROOT / "deploy/lightsail/compose.production.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn("COPY web/ ./web/", dockerfile)
        self.assertIn("ca-certificates", dockerfile)
        for variable in (
            "OPENAI_API_KEY",
            "OPENAI_MODEL",
            "APP_ACCESS_TOKEN",
            "ANALYSIS_RATE_LIMIT_PER_HOUR",
            "MAX_REQUEST_BYTES",
            "MAX_IMAGE_BYTES",
        ):
            self.assertIn(variable, compose)
        self.assertNotIn("OPENAI_API_KEY:", dockerfile)

    def test_proxy_limits_uploads_and_protects_sensitive_responses(self) -> None:
        caddyfile = (ROOT / "deploy/lightsail/Caddyfile").read_text(
            encoding="utf-8"
        )

        self.assertIn("request_body", caddyfile)
        self.assertIn("max_size", caddyfile)
        self.assertIn("Content-Security-Policy", caddyfile)
        self.assertIn("/service-worker.js", caddyfile)
        self.assertIn('Cache-Control "no-store"', caddyfile)

    def test_provisioner_generates_access_token_without_echoing_it(self) -> None:
        provisioner = (ROOT / "deploy/lightsail/provision-ubuntu.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("openssl rand -hex 32", provisioner)
        self.assertIn("install -m 0600", provisioner)
        self.assertNotIn('echo "${app_access_token}"', provisioner)
        self.assertIn("OPENAI_API_KEY=REPLACE_WITH_OPENAI_API_KEY", provisioner)

    def test_deployment_archive_excludes_credentials_and_clients(self) -> None:
        workflow = (ROOT / ".github/workflows/deploy-lightsail.yml").read_text(
            encoding="utf-8"
        )

        for excluded in ("--exclude=.env", "--exclude=Clients", "--exclude='*.pem'", "--exclude='*.key'"):
            self.assertIn(excluded, workflow)


if __name__ == "__main__":
    unittest.main()
