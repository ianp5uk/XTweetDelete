#!/usr/bin/env python3
"""
TweetDelete background deletion runner (desktop v1.0.4+).

Python port of the Android app's DeletionEngine.kt, for the same reason it
exists there.

Up to desktop v1.0.0 the delete loop ran as JavaScript in the browser tab.
Browsers throttle or freeze timers in tabs that are hidden, minimised or
behind other windows: Chrome/Edge "intensive throttling" cuts timers to
once a minute after 5 minutes hidden, and Edge "sleeping tabs" / Chrome
"Memory Saver" freeze the tab outright. The 15-minute rate-limit wait was a
timer loop that counted 500 ms ticks instead of checking the clock, so once
throttled it stretched to many hours, and in a frozen tab it never finished.
That is why a run could appear to stop after the first batch of 50. Whether
it happened depended on the browser and its settings, not on Windows vs
Linux (both builds shipped the same code).

From v1.0.4 the page still does login, fetching, filtering and the archive
step, then hands the final target list to this runner inside the local
helper process (the Windows tray app / the Linux systemd user service) and
only displays progress. The runner:

  * waits against the wall clock, so waits end on time;
  * paces 50 per 15 min per category (plus 1,000 per 24 h for likes),
    honours X's own x-rate-limit-remaining / x-rate-limit-reset headers,
    and on HTTP 429 retries the same item after the reset instead of
    recording it as failed;
  * retries network drop-outs with back-off (15 s doubling to 5 min);
  * refreshes the 2-hour access token itself and hands the rotated refresh
    token back to the page;
  * auto-pauses after 10 consecutive failures with the same HTTP status
    (e.g. API credits exhausted);
  * saves its state after every item, so if the helper is restarted (reboot,
    logout, tray Quit) the run resumes when it next starts.

Closing the browser window does not stop a run. Quitting the tray app
(Windows) or stopping the service (Linux) pauses it until next start.

Standard library only, so the Linux package keeps depending on nothing but
the system python3.
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.x.com"
MIN_15 = 15 * 60 * 1000
HOURS_24 = 24 * 60 * 60 * 1000
MAX_CONSECUTIVE_FAILURES = 10
MAX_NETWORK_BACKOFF = 5 * 60 * 1000

LIMITS = {
    "posts": [(50, MIN_15)],
    "reposts": [(50, MIN_15)],
    "likes": [(50, MIN_15), (1000, HOURS_24)],
}
SINGULAR = {"posts": "post/reply", "reposts": "repost", "likes": "like"}
PLURAL = {"posts": "posts/replies", "reposts": "reposts", "likes": "likes"}


def now_ms():
    return int(time.time() * 1000)


def default_state_dir():
    """Per-user, private location for the run state file (it contains the
    OAuth tokens for the duration of a run)."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "TweetDelete")
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "tweetdelete")


class _Network(Exception):
    pass


class DeletionRunner:
    STATE_FILE = "run_state.json"

    def __init__(self, state_dir=None, api_base=API, log=None):
        self.state_dir = state_dir or default_state_dir()
        self.api_base = api_base.rstrip("/")
        self.log = log or (lambda msg: sys.stderr.write("[runner] " + msg + "\n"))
        self.lock = threading.RLock()
        self.wake = threading.Event()  # set on pause/cancel to cut sleeps short
        self.thread = None
        self._reset_locked()
        # Transient, UI-only
        self.status_text = ""
        self.wait_until = 0
        # "limit" (normal rate-limit wait), "xdelay" (X still refused after a
        # wait ended) or "network". Drives the countdown wording in the UI.
        self.wait_kind = ""
        # True from the end of a rate-limit wait until X accepts a request again.
        self.awaiting_x = False

    @property
    def state_path(self):
        return os.path.join(self.state_dir, self.STATE_FILE)

    def _reset_locked(self):
        self.state = "idle"  # idle | running | finished
        self.items = []
        self.next_index = 0
        self.results = []
        self.deleted = 0
        self.failed = 0
        self.user_id = ""
        self.username = ""
        self.client_id = ""
        self.access_token = ""
        self.refresh_token = None
        self.expires_at = 0
        self.tokens_updated = False
        self.window_use = {}
        self.blocked_until = {}
        self.paused = False
        self.pause_reason = None
        self.cancelled = False
        self.error = None

    # ------------------------------------------------------------------
    # Public API (called from server.py request handlers)

    def restore(self):
        """Called once at helper start-up: resumes an interrupted run, if any."""
        with self.lock:
            if not os.path.exists(self.state_path):
                return
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    self._from_json(json.load(f))
            except Exception as e:
                self.log("Could not read saved run state (%s); discarding it" % e)
                try:
                    os.remove(self.state_path)
                except OSError:
                    pass
                self._reset_locked()
                return
            running = self.state == "running"
        if running:
            self.status_text = "Resuming interrupted run…"
            self._launch()

    def start(self, payload):
        """Starts a new run. Returns an error message, or None on success."""
        with self.lock:
            if self.state == "running":
                return "A run is already in progress."
            try:
                items = [
                    {"id": str(o["id"]), "category": o["category"], "created_at": o.get("created_at") or ""}
                    for o in payload["targets"]
                ]
                tokens = payload["tokens"]
                self._reset_locked()
                self.items = items
                self.user_id = str(payload["userId"])
                self.username = payload.get("username") or ""
                self.client_id = payload["clientId"]
                self.access_token = tokens["access_token"]
                self.refresh_token = tokens.get("refresh_token") or None
                self.expires_at = int(tokens.get("expires_at") or 0)
            except (KeyError, TypeError, ValueError) as e:
                self._reset_locked()
                return "Invalid run request: %s" % e
            self.state = "running"
            self._persist_locked()
        self.status_text = "Starting…"
        self._launch()
        return None

    def set_paused(self, paused):
        with self.lock:
            if self.state != "running":
                return
            self.paused = bool(paused)
            if not paused:
                self.pause_reason = None
            self._persist_locked()
        self.wake.set()

    def cancel(self):
        with self.lock:
            if self.state != "running":
                return
            self.cancelled = True
            self.paused = False
            self._persist_locked()
        self.wake.set()

    def clear(self):
        """Clears a finished run once the UI has shown/exported its results."""
        with self.lock:
            if self.state == "running":
                return
            self._reset_locked()
            try:
                os.remove(self.state_path)
            except OSError:
                pass

    def status(self):
        with self.lock:
            return {
                "state": self.state,
                "paused": self.paused,
                "pauseReason": self.pause_reason,
                "cancelled": self.cancelled,
                "total": len(self.items),
                "deleted": self.deleted,
                "failed": self.failed,
                "status": self.status_text,
                "waitUntil": self.wait_until,
                "waitKind": self.wait_kind,
                "awaitingX": self.awaiting_x,
                "now": now_ms(),
                "error": self.error,
                "username": self.username,
                "userId": self.user_id,
                "resultCount": len(self.results),
            }

    def results_from(self, start):
        with self.lock:
            return list(self.results[max(0, int(start)):])

    def take_updated_tokens(self):
        """Latest tokens once after the runner has refreshed them, else None."""
        with self.lock:
            if not self.tokens_updated:
                return None
            self.tokens_updated = False
            self._persist_locked()
            return {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at,
            }

    # ------------------------------------------------------------------

    def _launch(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.thread = threading.Thread(target=self._thread_main, name="tweetdelete-runner", daemon=True)
            self.thread.start()

    def _thread_main(self):
        try:
            self._run_loop()
        except Exception as e:  # pragma: no cover - defensive
            self.log("Deletion loop crashed: %r" % e)
            with self.lock:
                self.error = "Unexpected error: %s" % e
                self._finish_locked()
        finally:
            self.wait_until = 0

    def _is_paused(self):
        with self.lock:
            return self.paused

    def _is_cancelled(self):
        with self.lock:
            return self.cancelled

    def _sleep_until(self, deadline):
        """Wall-clock based sleep; wakes early on cancel."""
        while not self._is_cancelled():
            remaining = deadline - now_ms()
            if remaining <= 0:
                return
            self.wake.wait(min(remaining, 5000) / 1000.0)
            self.wake.clear()

    def _run_loop(self):
        consecutive_failures = 0
        last_fail_status = ""
        network_backoff = 15000

        while True:
            with self.lock:
                if self.cancelled or self.next_index >= len(self.items):
                    self._finish_locked()
                    return
                item = self.items[self.next_index]

            if self._is_paused():
                with self.lock:
                    self.status_text = self.pause_reason or "Paused. Click Continue to resume."
                self.wait_until = 0
                while self._is_paused() and not self._is_cancelled():
                    self.wake.wait(1.0)
                    self.wake.clear()
                continue

            wait = self._ms_until_free(item["category"])
            if wait > 0:
                until = now_ms() + wait
                self.wait_kind = "xdelay" if self.awaiting_x else "limit"
                self.wait_until = until
                self.status_text = (
                    "Waiting for X to accept resumption" if self.awaiting_x
                    else "Rate limit reached for %s" % PLURAL[item["category"]]
                )
                self._sleep_until(until)
                self.wait_until = 0
                if self._is_cancelled():
                    continue
                self.awaiting_x = True
                self.status_text = "Waiting for X to accept resumption…"
                continue

            if not self.awaiting_x:
                self.status_text = "Removing %s %s…" % (SINGULAR[item["category"]], item["id"])

            kind, value = self._attempt_delete(item)

            if kind == "rate_limited":
                with self.lock:
                    self.blocked_until[item["category"]] = value
                    self._persist_locked()
                # Loop round; _ms_until_free() now reports the wait.
            elif kind == "network":
                until = now_ms() + network_backoff
                self.wait_kind = "network"
                self.wait_until = until
                self.status_text = "Network problem (%s) — retrying" % value
                self._sleep_until(until)
                self.wait_until = 0
                network_backoff = min(network_backoff * 2, MAX_NETWORK_BACKOFF)
            elif kind == "auth_failed":
                with self.lock:
                    self.error = (
                        "Your X session expired and could not be refreshed. Connect to X again "
                        "and start a new run; items already removed stay removed."
                    )
                    self._finish_locked()
                return
            else:  # "done"
                ok, detail = value
                network_backoff = 15000
                self.awaiting_x = False
                with self.lock:
                    row = {
                        "id": item["id"],
                        "category": item["category"],
                        "created_at": item["created_at"],
                        "status": "deleted" if ok else "failed",
                    }
                    if not ok:
                        row["detail"] = detail or ""
                    self.results.append(row)
                    if ok:
                        self.deleted += 1
                    else:
                        self.failed += 1
                    self.next_index += 1
                    self._persist_locked()
                if ok:
                    consecutive_failures = 0
                else:
                    parts = (detail or "").split(" ")
                    code = parts[1] if len(parts) > 1 else ""
                    consecutive_failures = consecutive_failures + 1 if code == last_fail_status else 1
                    last_fail_status = code
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        # Something systemic (e.g. API credits exhausted, app
                        # permissions changed): stop hammering X and let the
                        # user look before carrying on.
                        with self.lock:
                            self.paused = True
                            self.pause_reason = (
                                "Paused automatically after %d failures in a row (%s). "
                                "Check the log, then click Continue or Cancel." % (consecutive_failures, detail)
                            )
                            self._persist_locked()
                        consecutive_failures = 0

    def _ms_until_free(self, category):
        with self.lock:
            now = now_ms()
            wait = self.blocked_until.get(category, 0) - now
            uses = [t for t in self.window_use.get(category, []) if now - t < HOURS_24]
            self.window_use[category] = uses
            for max_n, window_ms in LIMITS.get(category, []):
                in_window = [t for t in uses if now - t < window_ms]
                if len(in_window) >= max_n:
                    oldest_relevant = in_window[len(in_window) - max_n]
                    wait = max(wait, window_ms - (now - oldest_relevant) + 1000)
            return wait

    def _record_use(self, category):
        with self.lock:
            self.window_use.setdefault(category, []).append(now_ms())

    def _http(self, method, url, headers=None, body=None):
        """Returns (status, headers, body_text). Raises _Network on transport failure."""
        req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.headers, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            try:
                text = e.read().decode("utf-8", "replace")
            except Exception:
                text = ""
            finally:
                e.close()
            return e.code, e.headers, text
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", None) or e
            raise _Network(str(reason) or "connection failed")

    def _attempt_delete(self, item, allow_auth_retry=True):
        try:
            token = self._ensure_token()
        except _Network as e:
            return "network", str(e)
        if not token:
            return "auth_failed", None

        with self.lock:
            uid = self.user_id
        cat = item["category"]
        if cat == "posts":
            url = "%s/2/tweets/%s" % (self.api_base, item["id"])
        elif cat == "reposts":
            url = "%s/2/users/%s/retweets/%s" % (self.api_base, uid, item["id"])
        else:
            url = "%s/2/users/%s/likes/%s" % (self.api_base, uid, item["id"])

        try:
            status, headers, body = self._http("DELETE", url, {"Authorization": "Bearer " + token})
        except _Network as e:
            return "network", str(e)

        self._record_use(cat)
        now = now_ms()
        reset_ms = None
        remaining = None
        try:
            reset_ms = int(headers.get("x-rate-limit-reset")) * 1000
        except (TypeError, ValueError):
            pass
        try:
            remaining = int(headers.get("x-rate-limit-remaining"))
        except (TypeError, ValueError):
            pass

        if status == 429:
            until = reset_ms + 2000 if reset_ms and reset_ms > now else now + 60000
            return "rate_limited", until
        if remaining == 0 and reset_ms and reset_ms > now:
            with self.lock:
                self.blocked_until[cat] = reset_ms + 2000
        if status == 401 and allow_auth_retry:
            try:
                refreshed = self._refresh_tokens()
            except _Network as e:
                return "network", str(e)
            return self._attempt_delete(item, allow_auth_retry=False) if refreshed else ("auth_failed", None)
        if 200 <= status < 300:
            return "done", (True, None)
        reason = extract_error_reason(body)
        return "done", (False, ("HTTP %d — %s" % (status, reason)) if reason else "HTTP %d" % status)

    def _ensure_token(self):
        """Valid access token, refreshing if within 60 s of expiry; None if re-login needed."""
        with self.lock:
            tok, exp = self.access_token, self.expires_at
        if now_ms() < exp - 60000:
            return tok
        if self._refresh_tokens():
            with self.lock:
                return self.access_token
        return None

    def _refresh_tokens(self):
        """X rotates refresh tokens, so the new pair is saved immediately and handed back to the UI."""
        with self.lock:
            rt, cid = self.refresh_token, self.client_id
        if not rt:
            return False
        form = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": rt, "client_id": cid}).encode()
        status, _, body = self._http(
            "POST", self.api_base + "/2/oauth2/token",
            {"Content-Type": "application/x-www-form-urlencoded"}, form,
        )
        if not (200 <= status < 300):
            self.log("Token refresh failed: HTTP %d" % status)
            return False
        try:
            data = json.loads(body or "{}")
        except ValueError:
            return False
        new_access = data.get("access_token") or ""
        if not new_access:
            return False
        with self.lock:
            self.access_token = new_access
            if data.get("refresh_token"):
                self.refresh_token = data["refresh_token"]
            self.expires_at = now_ms() + int(data.get("expires_in") or 7200) * 1000
            self.tokens_updated = True
            self._persist_locked()
        return True

    def _finish_locked(self):
        self.state = "finished"
        self.paused = False
        self.status_text = ""
        self.wait_until = 0
        self._persist_locked()

    # ---- Persistence ----

    def _persist_locked(self):
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            tmp = self.state_path + ".tmp"
            # 0600: the file holds the OAuth tokens while a run is active.
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._to_json(), f)
            os.replace(tmp, self.state_path)
        except Exception as e:
            self.log("Could not save run state: %r" % e)

    def _to_json(self):
        return {
            "state": self.state,
            "items": self.items,
            "nextIndex": self.next_index,
            "results": self.results,
            "deleted": self.deleted,
            "failed": self.failed,
            "userId": self.user_id,
            "username": self.username,
            "clientId": self.client_id,
            "accessToken": self.access_token,
            "refreshToken": self.refresh_token,
            "expiresAt": self.expires_at,
            "tokensUpdated": self.tokens_updated,
            "windowUse": self.window_use,
            "blockedUntil": self.blocked_until,
            "paused": self.paused,
            "pauseReason": self.pause_reason,
            "cancelled": self.cancelled,
            "error": self.error,
        }

    def _from_json(self, o):
        self.state = o.get("state", "idle")
        self.items = [
            {"id": str(i["id"]), "category": i["category"], "created_at": i.get("created_at", "")}
            for i in o.get("items", [])
        ]
        self.next_index = int(o.get("nextIndex", 0))
        self.results = list(o.get("results", []))
        self.deleted = int(o.get("deleted", 0))
        self.failed = int(o.get("failed", 0))
        self.user_id = o.get("userId", "")
        self.username = o.get("username", "")
        self.client_id = o.get("clientId", "")
        self.access_token = o.get("accessToken", "")
        self.refresh_token = o.get("refreshToken") or None
        self.expires_at = int(o.get("expiresAt", 0))
        self.tokens_updated = bool(o.get("tokensUpdated", False))
        self.window_use = {k: [int(t) for t in v] for k, v in (o.get("windowUse") or {}).items()}
        self.blocked_until = {k: int(v) for k, v in (o.get("blockedUntil") or {}).items()}
        self.paused = bool(o.get("paused", False))
        self.pause_reason = o.get("pauseReason") or None
        self.cancelled = bool(o.get("cancelled", False))
        self.error = o.get("error") or None


def extract_error_reason(text):
    if not text or not text.strip():
        return None
    try:
        o = json.loads(text)
    except ValueError:
        return text[:200]
    if not isinstance(o, dict):
        return text[:200]
    errs = o.get("errors")
    if isinstance(errs, list) and errs and isinstance(errs[0], dict):
        e = errs[0]
        for k in ("message", "detail", "title"):
            if e.get(k):
                return e[k]
        return None
    joined = ": ".join(p for p in (o.get("title"), o.get("detail")) if p)
    return joined or None
