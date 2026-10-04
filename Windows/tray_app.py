#!/usr/bin/env python3
"""
TweetDelete tray app - the actual entry point built into the Windows .exe.

Responsibilities (and nothing else):
  1. Start the local helper server from server.py in a background thread
     (or detect one is already running and reuse it, if this is a second
     launch while the first is still active).
  2. Open the app in a compact app window (launch_window.py), falling back
     to the default browser.
  3. Show a system tray icon with "Open TweetDelete" and "Quit" so there's
     a normal, discoverable way to stop the background process - matching
     how other small Windows utilities behave, rather than leaving a
     process running with no visible control.

  4. (v1.0.4+) Show the background run's progress and the time of the next
     batch in the tray icon's tooltip. The deletion runner itself
     (runner.py) lives in this process, so a run keeps going with the
     browser window closed; Quit pauses it until TweetDelete next starts.

Login, fetching and filtering live in the browser JavaScript under public/.
"""
import os
import sys
import threading
import time
import webbrowser
import logging
from logging.handlers import RotatingFileHandler

import server as tweetdelete_server
import launch_window


def setup_logging():
    """Frozen (--noconsole) Windows builds have no terminal to print to, and
    sys.stdout/sys.stderr can even be None, which makes print() raise. Log
    to a file under the user's local app data instead, so there's somewhere
    to look if something goes wrong post-install.
    """
    log_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "TweetDelete")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "tweetdelete.log")

    handler = RotatingFileHandler(log_path, maxBytes=512_000, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger = logging.getLogger("tweetdelete")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    # Prevent crashes from any stray print()/stdout writes when frozen with
    # no console attached.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    return logger


log = setup_logging()


def load_icon_image():
    """Loads the tray icon. Uses the bundled icon.ico if present (both when
    frozen and when run from source with packaging/icon.ico built), and
    falls back to a plain generated square so the app still runs even if
    the icon is missing for some reason.
    """
    from PIL import Image

    candidates = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(os.path.join(sys._MEIPASS, "icon.ico"))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "packaging", "icon.ico"))

    for path in candidates:
        if os.path.exists(path):
            return Image.open(path)

    log.warning("icon.ico not found in any candidate path, using a plain fallback icon")
    img = Image.new("RGBA", (64, 64), (1, 105, 111, 255))
    return img


HELP_PDF_NAME = "TweetDelete for Windows.pdf"


def help_pdf_path():
    """Locates the help PDF next to the rest of the bundled app files (it
    lives in public/, alongside the web app, so PyInstaller picks it up
    automatically via the existing public/ -> public/ data mapping in
    build.spec - no separate bundling step needed). Returns None if it
    hasn't been added yet, so callers can fail gracefully instead of
    crashing the whole tray app over a missing document.
    """
    path = os.path.join(tweetdelete_server.PUBLIC_DIR, HELP_PDF_NAME)
    return path if os.path.exists(path) else None


def open_help_pdf():
    path = help_pdf_path()
    if not path:
        log.warning("Help menu clicked but %s was not found at %s", HELP_PDF_NAME, tweetdelete_server.PUBLIC_DIR)
        return
    try:
        os.startfile(path)  # noqa: this module only ever runs on Windows
    except Exception:
        log.exception("Failed to open help PDF")


def tooltip_text(st):
    """Tray tooltip (Windows limits it to 127 characters)."""
    if not st or st.get("state") != "running":
        return "TweetDelete"
    done = st.get("deleted", 0) + st.get("failed", 0)
    text = "TweetDelete: %d/%d done" % (done, st.get("total", 0))
    if st.get("paused"):
        text += ", paused"
    elif st.get("waitUntil", 0) > time.time() * 1000:
        text += ", next batch at " + time.strftime("%H:%M", time.localtime(st["waitUntil"] / 1000))
    return text[:127]


def open_app(url):
    try:
        launch_window.open_app(url, log=log.info)
    except Exception:
        log.exception("App window launch failed; using default browser")
        webbrowser.open(url)


def main():
    import pystray
    from pystray import MenuItem as Item

    log.info("Starting TweetDelete tray app")

    existing_port = tweetdelete_server.find_running_instance()
    if existing_port:
        log.info("Found an already-running instance on port %s, reusing it", existing_port)
        url = f"http://127.0.0.1:{existing_port}/"
        open_app(url)
        # Nothing more to do - the other instance owns the tray icon.
        return

    httpd = tweetdelete_server.build_server(log=log.info)
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    log.info("Serving at %s", url)

    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    open_app(url)

    def on_open(icon, item):
        open_app(url)

    def on_help(icon, item):
        open_help_pdf()

    def on_quit(icon, item):
        log.info("Quit requested from tray menu")
        icon.stop()
        httpd.shutdown()

    icon_image = load_icon_image()
    tray_icon = pystray.Icon(
        "tweetdelete",
        icon_image,
        "TweetDelete",
        menu=pystray.Menu(
            Item("Open TweetDelete", on_open, default=True),
            Item("Help", on_help),
            Item("Quit", on_quit),
        ),
    )

    def update_tooltip():
        last = None
        while True:
            try:
                text = tooltip_text(tweetdelete_server.get_runner().status())
                if text != last:
                    tray_icon.title = text
                    last = text
            except Exception:
                log.exception("Tooltip update failed")
            time.sleep(5)

    threading.Thread(target=update_tooltip, daemon=True).start()

    try:
        tray_icon.run()
    except Exception:
        log.exception("Tray icon crashed")
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Fatal error in TweetDelete tray app")
        raise
