"""Tests for server.py's version reporting and help-guide selection.
Standard library only.

Run from desktop/:   python3 -m unittest discover -s tests -v
"""
import json
import http.client
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import server  # noqa: E402


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Keep the runner's state file (and the OAuth tokens it holds)
        # well away from the real per-user state directory.
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["TWEETDELETE_STATE_DIR"] = cls.tmp.name
        cls.httpd = server.build_server(port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()
        del os.environ["TWEETDELETE_STATE_DIR"]
        server.RUNNER = None

    def test_version_constant_is_set(self):
        # Guard against someone emptying the constant - the footer, the .deb
        # metadata and the .exe version resource all read it.
        self.assertRegex(server.VERSION, r"^\d+\.\d+\.\d+")

    def test_health_reports_version(self):
        # The footer displays whatever this endpoint returns, so it must
        # report the exact VERSION constant, not something stale.
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/__tweetdelete_health", timeout=5
        ) as resp:
            body = json.load(resp)
        self.assertEqual(body["app"], "tweetdelete")
        self.assertEqual(body["version"], server.VERSION)

    def request_static(self, path, method="GET", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_ui_assets_require_revalidation(self):
        for path in ("/", "/index.html", "/callback.html", "/app.js",
                     "/oauth.js", "/runner.js", "/style.css", "/app.js?build=test"):
            for method in ("GET", "HEAD"):
                with self.subTest(path=path, method=method):
                    status, headers, _ = self.request_static(path, method)
                    self.assertEqual(status, 200)
                    self.assertEqual(headers["Cache-Control"], "no-cache")
                    self.assertIn("Last-Modified", headers)

    def test_unchanged_asset_returns_304_with_cache_policy(self):
        _, initial_headers, _ = self.request_static("/app.js")
        status, headers, body = self.request_static(
            "/app.js", headers={"If-Modified-Since": initial_headers["Last-Modified"]}
        )
        self.assertEqual(status, 304)
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertEqual(body, b"")

    def test_changed_asset_returns_updated_content(self):
        with tempfile.TemporaryDirectory() as public_dir:
            asset = os.path.join(public_dir, "app.js")
            with open(asset, "wb") as f:
                f.write(b"old content")
            os.utime(asset, (1_700_000_000, 1_700_000_000))
            with mock.patch.object(server, "PUBLIC_DIR", public_dir):
                _, headers, _ = self.request_static("/app.js")
                with open(asset, "wb") as f:
                    f.write(b"new content")
                os.utime(asset, (1_700_000_010, 1_700_000_010))
                status, updated_headers, body = self.request_static(
                    "/app.js", headers={"If-Modified-Since": headers["Last-Modified"]}
                )
            self.assertEqual(status, 200)
            self.assertEqual(updated_headers["Cache-Control"], "no-cache")
            self.assertEqual(body, b"new content")

    def test_health_keeps_no_store(self):
        status, headers, _ = self.request_static("/__tweetdelete_health")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_non_ui_asset_does_not_get_ui_cache_policy(self):
        with tempfile.TemporaryDirectory() as public_dir:
            with open(os.path.join(public_dir, "guide.pdf"), "wb") as f:
                f.write(b"%PDF-test")
            with mock.patch.object(server, "PUBLIC_DIR", public_dir):
                status, headers, _ = self.request_static("/guide.pdf")
            self.assertEqual(status, 200)
            self.assertNotIn("Cache-Control", headers)

    def test_health_pdf_candidates_prefer_windows(self):
        with mock.patch.object(server.sys, "platform", "win32"):
            candidates = server.help_pdf_candidates()
        self.assertEqual(candidates[0], "TweetDelete for Windows.pdf")

    def test_health_pdf_candidates_prefer_linux(self):
        with mock.patch.object(server.sys, "platform", "linux"):
            candidates = server.help_pdf_candidates()
        self.assertNotIn("TweetDelete for Windows.pdf", candidates[:3])
        self.assertEqual(
            candidates[:3],
            ["TweetDelete for Linux.pdf", "TweetDelete for Debian.pdf", "TweetDelete for Ubuntu.pdf"],
        )


if __name__ == "__main__":
    unittest.main()
