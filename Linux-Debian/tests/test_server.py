"""Tests for server.py's version reporting and help-guide selection.
Standard library only.

Run from desktop/:   python3 -m unittest discover -s tests -v
"""
import json
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
