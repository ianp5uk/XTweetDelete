#!/usr/bin/env python3
"""
Opens TweetDelete in a compact app window sized to the app (desktop v1.0.4+).

Before v1.0.4 the app was opened with the OS "open URL" call, i.e. a normal
tab in a full-size browser window, so on a large or high-resolution display
the 720 px-wide app sat in the middle of a mostly empty window.

Chromium-family browsers (Edge, Chrome, Chromium, Brave, Vivaldi) can open a
URL as a standalone "app" window, with no tabs or address bar, at a chosen
size (--app / --window-size). This module does that, using the normal
browser profile, so any Client ID and X login already saved in that browser
are kept. The page itself then fine-tunes the size in CSS pixels (see
fitAppWindow() in app.js), which keeps it correct under any display scaling.

Firefox has no equivalent of --app, so if no Chromium-family browser is
installed this falls back to the previous behaviour (default browser tab).

Browser choice, in order:
  1. launcher.json "browser" setting, or the TWEETDELETE_BROWSER environment
     variable: a full path/command for a Chromium-family browser, or
     "default" to always use the old plain-tab behaviour.
  2. The system default browser, if it is Chromium-family.
  3. Any installed Chromium-family browser (Edge first on Windows, as it is
     always present on Windows 10/11).
  4. Plain default-browser tab.

launcher.json lives in %LOCALAPPDATA%\\TweetDelete\\ on Windows and
~/.config/tweetdelete/ on Linux, and may also set "width" and "height"
(starting window size in pixels; the page corrects it after loading).

Usage: python3 launch_window.py [URL]      (used by the Linux launcher)
       from launch_window import open_app  (used by the Windows tray app)
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import webbrowser

DEFAULT_URL = "http://127.0.0.1:8765/"
DEFAULT_WIDTH = 800
DEFAULT_HEIGHT = 940

IS_WINDOWS = sys.platform.startswith("win")


def config_path():
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "TweetDelete", "launcher.json")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "tweetdelete", "launcher.json")


def load_config():
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            cfg = json.load(f)
            return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- Windows

WIN_PROGIDS = ("MSEdgeHTM", "ChromeHTML", "BraveHTML", "VivaldiHTM", "ChromiumHTM")


def _win_default_browser():
    try:
        import winreg
        key = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            prog_id = winreg.QueryValueEx(k, "ProgId")[0]
        if not prog_id.startswith(WIN_PROGIDS):
            return None
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id + r"\shell\open\command") as k:
            cmd = winreg.QueryValueEx(k, "")[0]
        exe = shlex.split(cmd, posix=False)[0].strip('"')
        return [exe] if os.path.exists(exe) else None
    except Exception:
        return None


def _win_installed_browsers():
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    rel = [
        r"Microsoft\Edge\Application\msedge.exe",
        r"Google\Chrome\Application\chrome.exe",
        r"BraveSoftware\Brave-Browser\Application\brave.exe",
        r"Vivaldi\Application\vivaldi.exe",
        r"Chromium\Application\chrome.exe",
    ]
    for r in rel:
        for base in (pf86, pf, local):
            if base:
                p = os.path.join(base, r)
                if os.path.exists(p):
                    yield [p]


# ---------------------------------------------------------------- Linux

LINUX_CHROMIUM_CMDS = (
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "brave-browser", "brave", "microsoft-edge", "microsoft-edge-stable",
    "vivaldi", "vivaldi-stable",
)
LINUX_FLATPAKS = ("com.google.Chrome", "org.chromium.Chromium", "com.brave.Browser", "com.microsoft.Edge")
CHROMIUM_HINTS = ("chrome", "chromium", "brave", "edge", "vivaldi")


def _linux_default_browser():
    try:
        out = subprocess.run(
            ["xdg-settings", "get", "default-web-browser"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip().lower()
    except Exception:
        return None
    if not out or not any(h in out for h in CHROMIUM_HINTS):
        return None
    # Flatpak desktop IDs look like com.google.Chrome.desktop.
    for app_id in LINUX_FLATPAKS:
        if out == app_id.lower() + ".desktop" and shutil.which("flatpak"):
            return ["flatpak", "run", app_id]
    # Map the desktop file to a command on PATH (e.g. google-chrome.desktop,
    # chromium_chromium.desktop for the Ubuntu snap, brave-browser.desktop).
    for cmd in LINUX_CHROMIUM_CMDS:
        if cmd.split("-")[0] in out and shutil.which(cmd):
            return [shutil.which(cmd)]
    return None


def _linux_installed_browsers():
    for cmd in LINUX_CHROMIUM_CMDS:
        p = shutil.which(cmd)
        if p:
            yield [p]
    if shutil.which("flatpak"):
        for app_id in LINUX_FLATPAKS:
            try:
                if subprocess.run(["flatpak", "info", app_id], capture_output=True, timeout=3).returncode == 0:
                    yield ["flatpak", "run", app_id]
            except Exception:
                pass


# ---------------------------------------------------------------- common

def find_browser(cfg):
    choice = (cfg.get("browser") or os.environ.get("TWEETDELETE_BROWSER") or "").strip()
    if choice.lower() == "default":
        return None
    if choice:
        parts = shlex.split(choice, posix=not IS_WINDOWS)
        if parts and (os.path.exists(parts[0].strip('"')) or shutil.which(parts[0])):
            return [p.strip('"') for p in parts]
    if IS_WINDOWS:
        return _win_default_browser() or next(_win_installed_browsers(), None)
    return _linux_default_browser() or next(_linux_installed_browsers(), None)


def open_app(url=DEFAULT_URL, log=None):
    """Opens the app window. Returns True if an app window was launched,
    False if it fell back to a plain browser tab."""
    log = log or (lambda msg: None)
    cfg = load_config()
    width = int(cfg.get("width") or DEFAULT_WIDTH)
    height = int(cfg.get("height") or DEFAULT_HEIGHT)

    browser = find_browser(cfg)
    if browser:
        args = browser + [
            "--app=" + url,
            "--window-size=%d,%d" % (width, height),
            "--no-first-run",
            "--no-default-browser-check",
        ]
        try:
            kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if IS_WINDOWS:
                kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen(args, **kwargs)
            log("Opened app window with %s" % browser[0])
            return True
        except Exception as e:
            log("Could not start %s (%s); falling back to default browser" % (browser[0], e))

    webbrowser.open(url)
    log("Opened in default browser tab")
    return False


if __name__ == "__main__":
    open_app(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL)
