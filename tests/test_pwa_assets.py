from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


class PWAAssetTests(unittest.TestCase):
    def test_app_shell_is_installable_and_contains_explicit_consent(self) -> None:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        manifest = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))

        self.assertIn('rel="manifest"', html)
        self.assertIn('id="analysis-consent"', html)
        self.assertIn('id="compare-consent"', html)
        self.assertIn('id="profile-form"', html)
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["scope"], "/")
        self.assertTrue(manifest["icons"])

    def test_frontend_avoids_dynamic_html_and_strips_private_binary_data(self) -> None:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        app = (WEB / "app.js").read_text(encoding="utf-8")

        for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function"):
            self.assertNotIn(unsafe, app)
        self.assertIn("window.location.hash", app)
        self.assertIn("window.history.replaceState", app)
        self.assertIn('headers.set("Authorization", `Bearer ${token}`)', app)
        self.assertRegex(app, re.compile(r"if \(/image\|photo\|thumbnail/i\.test\(keyName\)\) return undefined"))
        self.assertIn("delete payload.image_data_url", app)
        self.assertIn("profile: sanitizeForStorage(state.profile)", app)
        self.assertNotIn("token: accessToken()", app)
        referenced_ids = set(re.findall(r'dom\["([^"]+)"\]', app))
        declared_ids = set(re.findall(r'\bid="([^"]+)"', html))
        self.assertTrue(referenced_ids.issubset(declared_ids), referenced_ids - declared_ids)

    def test_service_worker_never_caches_api_responses_and_refreshes_assets(self) -> None:
        worker = (WEB / "service-worker.js").read_text(encoding="utf-8")

        self.assertIn('url.pathname.startsWith("/api/")', worker)
        self.assertIn('request.method !== "GET"', worker)
        asset_branch = worker.split('if (request.mode === "navigate")', 1)[1]
        self.assertLess(asset_branch.find("fetch(request)"), asset_branch.find("caches.match(request)"))


if __name__ == "__main__":
    unittest.main()
