#!/usr/bin/env python3
"""
TweetDelete local helper server.

Why this exists: X's API does not send Access-Control-Allow-Origin headers,
so a browser calling api.x.com directly from JavaScript is blocked by CORS
(this has been an open X API limitation since 2016 - see
https://devcommunity.x.com/t/twitter-api-v2-public-client-no-access-control-allow-origin-header-present-cors/170402).

This script does exactly two things and nothing else:
  1. Serves the static files in ./public (the actual app: HTML/CSS/JS).
  2. Proxies any request under /api/x/<path> to https://api.x.com/<path>,
     forwarding method, headers, and body verbatim, and returns the response
     to the browser as same-origin (no CORS problem, because the browser is
     now only ever talking to 127.0.0.1).

No business logic lives here. Every decision (what to delete, filters,
pacing, confirmation, progress, OAuth token handling) happens in the
browser JavaScript in ./public. This script only exists to work around the
X API's lack of CORS support and to host the OAuth callback page over
http://127.0.0.1 (required, because X does not allow file:// redirect URIs).

Usage as a standalone script:
    python3 server.py [port]

Usage as a module (used by the Windows tray app build - see packaging/):
    from server import build_server
    httpd = build_server(port=8765)
    httpd.serve_forever()   # call httpd.shutdown() from another thread to stop
"""
import http.server
import os
import sys
import urllib.request
import urllib.error

DEFAULT_PORT = int(os.environ.get("PORT", "8765"))
UPSTREAM = "https://api.x.com"

# Headers that must not be forwarded as-is between hop-by-hop boundaries.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


def resource_dir():
    """Directory containing this script's own files. Handles both a normal
    checkout and a PyInstaller-frozen executable, where bundled data files
    are unpacked to a temp folder referenced by sys._MEIPASS at runtime.
    See https://pyinstaller.org/en/stable/runtime-information.html
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


PUBLIC_DIR = os.path.join(resource_dir(), "public")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=PUBLIC_DIR, **kwargs)

    # Silence default noisy logging format, keep it short.
    def log_message(self, fmt, *args):
        sys.stderr.write("[server] " + (fmt % args) + "\n")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/api/x/"):
            self._proxy("GET")
        elif self.path == "/__tweetdelete_health":
            payload = b'{"app":"tweetdelete"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif self.path == "/api/help":
            self._serve_help_pdf()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/x/"):
            self._proxy("POST")
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/api/x/"):
            self._proxy("DELETE")
        else:
            self.send_error(404)

    def do_PUT(self):
        if self.path.startswith("/api/x/"):
            self._proxy("PUT")
        else:
            self.send_error(404)

    def _send_cors_headers(self):
        # Not strictly required since the browser only ever calls same-origin
        # 127.0.0.1, but harmless to include and helps if this is ever opened
        # from a different local port during development.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def _serve_help_pdf(self):
        # The same public/ folder ships in both the Windows and Linux
        # builds, but each platform's help guide has a different filename
        # (and different content - install/uninstall steps differ). Rather
        # than hardcoding one filename into the HTML, redirect to whichever
        # one is actually present in this build, so the same footer link
        # works correctly regardless of which package built it.
        candidates = [
            "TweetDelete for Windows.pdf",
            "TweetDelete for Linux.pdf",
            "TweetDelete for Debian.pdf",
            "TweetDelete for Ubuntu.pdf",
        ]
        for name in candidates:
            if os.path.exists(os.path.join(PUBLIC_DIR, name)):
                from urllib.parse import quote
                self.send_response(302)
                self.send_header("Location", "/" + quote(name))
                self.end_headers()
                return
        self.send_error(404, "No help guide has been added to this build yet.")

    def _proxy(self, method):
        upstream_path = self.path[len("/api/x") :]  # keep leading slash
        url = UPSTREAM + upstream_path

        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None

        fwd_headers = {}
        for key, value in self.headers.items():
            if key.lower() not in HOP_BY_HOP and key.lower() != "content-length":
                fwd_headers[key] = value

        req = urllib.request.Request(url, data=body, headers=fwd_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                self._relay(resp.status, resp.headers, resp.read())
        except urllib.error.HTTPError as e:
            self._relay(e.code, e.headers, e.read())
        except Exception as e:
            payload = ('{"error":"proxy_failed","detail":%r}' % str(e)).encode("utf-8")
            self.send_response(502)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def _relay(self, status, headers, body):
        self.send_response(status)
        self._send_cors_headers()
        for key, value in headers.items():
            if key.lower() not in HOP_BY_HOP:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def is_port_free(port, host="127.0.0.1"):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) != 0


def find_free_port(preferred=DEFAULT_PORT, attempts=20):
    """Returns `preferred` if free, otherwise the next free port after it.
    Used by the tray app so a second launch (or a port already taken by
    something else) doesn't just crash silently.
    """
    port = preferred
    for _ in range(attempts):
        if is_port_free(port):
            return port
        port += 1
    raise RuntimeError(f"No free port found near {preferred}")


def find_running_instance(preferred=DEFAULT_PORT, attempts=20):
    """Checks whether a TweetDelete server is already listening nearby (e.g.
    from a previous launch that's still running). Returns the port it's on,
    or None. Used by the tray app to avoid starting a second server and
    instead just re-open the browser to the existing one.
    """
    import urllib.request

    port = preferred
    for _ in range(attempts):
        if not is_port_free(port):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/__tweetdelete_health", timeout=0.5) as resp:
                    if resp.status == 200 and b"tweetdelete" in resp.read():
                        return port
            except Exception:
                pass  # Something else is using this port — not us, keep looking.
        port += 1
    return None


def build_server(port=None):
    """Creates (but does not start) the HTTP server. Call .serve_forever()
    to run it, and .shutdown() from another thread to stop it cleanly.
    """
    os.makedirs(PUBLIC_DIR, exist_ok=True)
    chosen_port = port if port is not None else find_free_port(DEFAULT_PORT)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", chosen_port), Handler)
    return server


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else None
    server = build_server(port)
    actual_port = server.server_address[1]
    print(f"TweetDelete running at http://127.0.0.1:{actual_port}/")
    print(f"OAuth callback / redirect URI to register in your X app: http://127.0.0.1:{actual_port}/callback.html")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
