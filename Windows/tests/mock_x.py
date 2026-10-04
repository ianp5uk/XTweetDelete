"""Minimal mock of the X API endpoints TweetDelete uses, for tests only."""
import http.server
import json
import threading
import time
from urllib.parse import urlsplit, parse_qs


class MockX:
    def __init__(self, n_posts=12, batch=None, window_s=3):
        self.lock = threading.Lock()
        self.posts = [
            {"id": str(1000 + i), "text": "p%d" % i, "created_at": "2024-01-%02dT10:00:00.000Z" % (i % 28 + 1)}
            for i in range(n_posts)
        ]
        self.deleted = []
        self.batch = batch            # server-side cap per window (None = unlimited)
        self.window_s = window_s
        self.window_start = time.time()
        self.window_used = 0
        self.fail_next_429 = 0        # force this many 429s
        self.fail_ids = set()         # ids that return 403
        self.token = "AT1"
        self.refresh = "RT1"
        self.refresh_calls = 0
        self.expire_token_after = None  # after N deletes, current token stops working
        self.httpd = None

    def start(self):
        mock = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None, headers=None):
                body = json.dumps(obj or {}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                for k, v in (headers or {}).items():
                    self.send_header(k, str(v))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _auth_ok(self):
                return self.headers.get("Authorization") == "Bearer " + mock.token

            def do_GET(self):
                u = urlsplit(self.path)
                if not self._auth_ok():
                    return self._send(401, {"title": "Unauthorized"})
                if u.path == "/2/users/me":
                    return self._send(200, {"data": {"id": "42", "username": "tester", "name": "T",
                                                     "public_metrics": {"tweet_count": len(mock.posts), "like_count": 0}}})
                if u.path == "/2/users/42/tweets":
                    with mock.lock:
                        live = [p for p in mock.posts if p["id"] not in mock.deleted]
                    return self._send(200, {"data": live, "meta": {"result_count": len(live)}})
                return self._send(404)

            def do_POST(self):
                u = urlsplit(self.path)
                n = int(self.headers.get("Content-Length") or 0)
                form = parse_qs(self.rfile.read(n).decode())
                if u.path == "/2/oauth2/token":
                    with mock.lock:
                        if form.get("refresh_token", [""])[0] != mock.refresh:
                            return self._send(400, {"error": "invalid_request"})
                        mock.refresh_calls += 1
                        mock.token = "AT%d" % (mock.refresh_calls + 1)
                        mock.refresh = "RT%d" % (mock.refresh_calls + 1)
                        return self._send(200, {"access_token": mock.token, "refresh_token": mock.refresh,
                                                "expires_in": 7200, "token_type": "bearer"})
                return self._send(404)

            def do_DELETE(self):
                u = urlsplit(self.path)
                if not self._auth_ok():
                    return self._send(401, {"title": "Unauthorized", "detail": "token expired"})
                tid = u.path.rsplit("/", 1)[-1]
                with mock.lock:
                    now = time.time()
                    if now - mock.window_start >= mock.window_s:
                        mock.window_start, mock.window_used = now, 0
                    reset = int(mock.window_start + mock.window_s) + 1
                    if mock.fail_next_429 > 0:
                        mock.fail_next_429 -= 1
                        return self._send(429, {"title": "Too Many Requests"},
                                          {"x-rate-limit-reset": int(now) + 2, "x-rate-limit-remaining": 0})
                    if mock.batch is not None and mock.window_used >= mock.batch:
                        return self._send(429, {"title": "Too Many Requests"},
                                          {"x-rate-limit-reset": reset, "x-rate-limit-remaining": 0})
                    mock.window_used += 1
                    if tid in mock.fail_ids:
                        return self._send(403, {"errors": [{"message": "You are not allowed to delete this Tweet."}]})
                    mock.deleted.append(tid)
                    if mock.expire_token_after is not None and len(mock.deleted) >= mock.expire_token_after:
                        mock.expire_token_after = None
                        mock.token = "EXPIRED"
                    rem = (mock.batch - mock.window_used) if mock.batch is not None else 99
                    return self._send(200, {"data": {"deleted": True}},
                                      {"x-rate-limit-reset": reset, "x-rate-limit-remaining": rem})

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
