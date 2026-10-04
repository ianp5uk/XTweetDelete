"""Tests for runner.py against a mock X API. Standard library only.

Run from desktop/:   python3 -m unittest discover -s tests -v
Rate-limit windows are shrunk from 15 min to a few seconds for speed.
"""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import runner  # noqa: E402
from mock_x import MockX  # noqa: E402


def payload(mock, n=None, category="posts", expires_in_ms=3_600_000):
    posts = mock.posts if n is None else mock.posts[:n]
    return {
        "targets": [{"id": p["id"], "category": category, "created_at": p["created_at"]} for p in posts],
        "userId": "42",
        "username": "tester",
        "clientId": "cid",
        "tokens": {"access_token": mock.token, "refresh_token": mock.refresh,
                   "expires_at": runner.now_ms() + expires_in_ms},
    }


def wait_for(pred, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self._limits = runner.LIMITS
        # 4 per 2 s instead of 50 per 15 min.
        runner.LIMITS = {"posts": [(4, 2000)], "reposts": [(4, 2000)], "likes": [(4, 2000), (1000, runner.HOURS_24)]}
        self.tmp = tempfile.TemporaryDirectory()
        self.mock = MockX(n_posts=10)
        self.base = self.mock.start()

    def tearDown(self):
        runner.LIMITS = self._limits
        self.mock.stop()
        self.tmp.cleanup()

    def make(self):
        return runner.DeletionRunner(state_dir=self.tmp.name, api_base=self.base, log=lambda m: None)

    def test_preemptive_wait_with_countdown_then_completes(self):
        r = self.make()
        self.assertIsNone(r.start(payload(self.mock)))
        # After 4 deletions it must wait, exposing a future waitUntil for the countdown.
        self.assertTrue(wait_for(lambda: r.status()["waitUntil"] > runner.now_ms()))
        st = r.status()
        self.assertEqual(st["waitKind"], "limit")
        self.assertIn("Rate limit reached for posts/replies", st["status"])
        self.assertEqual(st["deleted"], 4)
        self.assertTrue(wait_for(lambda: r.status()["state"] == "finished"))
        st = r.status()
        self.assertEqual((st["deleted"], st["failed"]), (10, 0))
        self.assertEqual(len(self.mock.deleted), 10)

    def test_429_retries_same_item_not_failed(self):
        self.mock.fail_next_429 = 2
        r = self.make()
        r.start(payload(self.mock, n=3))
        self.assertTrue(wait_for(lambda: r.status()["state"] == "finished"))
        st = r.status()
        self.assertEqual((st["deleted"], st["failed"]), (3, 0))

    def test_server_side_cap_shows_waiting_for_x(self):
        # X allows fewer than our own pacing assumes: 2 per 3 s window.
        runner.LIMITS = {"posts": [(50, 900000)]}
        self.mock.batch = 2
        self.mock.window_s = 3
        r = self.make()
        r.start(payload(self.mock, n=5))
        self.assertTrue(wait_for(lambda: r.status()["waitUntil"] > runner.now_ms()))
        self.assertTrue(wait_for(lambda: r.status()["state"] == "finished", timeout=40))
        self.assertEqual(r.status()["deleted"], 5)

    def test_token_refresh_and_handback(self):
        self.mock.expire_token_after = 2
        r = self.make()
        r.start(payload(self.mock, n=5))
        self.assertTrue(wait_for(lambda: r.status()["state"] == "finished"))
        self.assertEqual(r.status()["deleted"], 5)
        t = r.take_updated_tokens()
        self.assertEqual(t["refresh_token"], self.mock.refresh)
        self.assertIsNone(r.take_updated_tokens())  # handed back once

    def test_failures_recorded_and_auto_pause(self):
        self.mock.posts = [{"id": str(i), "created_at": ""} for i in range(12)]
        self.mock.fail_ids = {str(i) for i in range(12)}
        runner.LIMITS = {"posts": [(100, 2000)]}
        r = self.make()
        r.start(payload(self.mock))
        self.assertTrue(wait_for(lambda: r.status()["paused"]))
        st = r.status()
        self.assertEqual(st["failed"], 10)
        self.assertIn("Paused automatically after 10 failures", st["status"])
        self.assertIn("not allowed", r.results_from(0)[0]["detail"])
        r.cancel()
        self.assertTrue(wait_for(lambda: r.status()["state"] == "finished"))
        self.assertTrue(r.status()["cancelled"])

    def test_resume_after_restart(self):
        r = self.make()
        r.start(payload(self.mock))
        self.assertTrue(wait_for(lambda: r.status()["waitUntil"] > runner.now_ms()))
        # Simulate the helper being killed mid-wait: a fresh instance restores.
        with open(r.state_path) as f:
            saved = f.read()
        r.cancelled = True  # stop the old thread (it writes to its own dir)
        r.wake.set()
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        r2 = runner.DeletionRunner(state_dir=other.name, api_base=self.base, log=lambda m: None)
        with open(r2.state_path, "w") as f:
            f.write(saved)
        r2.restore()
        self.assertTrue(wait_for(lambda: r2.status()["state"] == "finished"))
        self.assertEqual(r2.status()["deleted"], 10)
        self.assertEqual(len(set(self.mock.deleted)), 10)
        if os.name == "posix":
            self.assertEqual(os.stat(r2.state_path).st_mode & 0o777, 0o600)

    def test_network_failure_retries(self):
        r = runner.DeletionRunner(state_dir=self.tmp.name, api_base="http://127.0.0.1:9", log=lambda m: None)
        r.start(payload(self.mock, n=1))
        self.assertTrue(wait_for(lambda: r.status()["waitKind"] == "network"))
        self.assertIn("Network problem", r.status()["status"])
        r.cancel()
        self.assertTrue(wait_for(lambda: r.status()["state"] == "finished", timeout=10))


if __name__ == "__main__":
    unittest.main()
