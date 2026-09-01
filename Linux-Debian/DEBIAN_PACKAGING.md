# Building the Ubuntu/Debian package

Turns TweetDelete into a normal Ubuntu app: an entry in the Activities/
Applications menu, a background service managed by `systemd --user` that
starts automatically at login, and clean `apt` install/uninstall. Unlike
the Windows build, **no Python interpreter is bundled** — the package
depends on Ubuntu's own `python3` via `apt`. That's a deliberate difference
from Windows, not an oversight; see below.

## Why no bundled Python here, unlike Windows

The Windows build bundles Python because there's no reliable guarantee of
a compatible interpreter being present, and no OS-level mechanism to
declare "this app needs Python 3.10+" and have it enforced. Ubuntu is a
different situation entirely:

- Ubuntu 24.04 guarantees Python 3 is present — it's load-bearing for `apt`
  itself.
- `server.py` has zero third-party dependencies (pure standard library),
  so there's no version-drift risk to protect against in the first place.
- `.deb`'s `Depends:` field is the actual correct tool for "require a
  compatible version" — declared in `packaging/linux/control` as
  `python3 (>= 3.8)`, enforced automatically by `apt`.
- A bundled interpreter would never get security patches unless you
  noticed a Python CVE and reshipped. Depending on system `python3` means
  it's patched automatically whenever Ubuntu ships a security update.

## What gets built, and how it fits together

```
packaging/linux/
  control                build metadata (name, version, Depends:, description)
  postinst               enables + best-effort starts the background service
  prerm                  best-effort stops + disables it before removal
  tweetdelete.service    systemd --user unit (the background server)
  tweetdelete.desktop    Applications-menu launcher entry
  tweetdelete-launcher.sh   installed as /usr/bin/tweetdelete - starts the
                            service if needed, opens the browser
  copyright              Debian policy expects this file to exist
  make_icon_png.py       converts the shared icon.ico to PNG for Linux
  build_deb.sh           assembles everything above into the .deb
```

Installed layout on the target machine:

```
/usr/lib/tweetdelete/server.py        same server.py used everywhere else
/usr/lib/tweetdelete/public/           the web app (same folder as Windows)
/usr/lib/systemd/user/tweetdelete.service
/usr/bin/tweetdelete                   launcher script
/usr/share/applications/tweetdelete.desktop
/usr/share/icons/hicolor/256x256/apps/tweetdelete.png
/usr/share/doc/tweetdelete/{copyright,changelog.gz}
```

## Adding the help guide

Same pattern as Windows, different filename. Drop your finished PDF into
`public/` and name it exactly one of these (case-sensitive — the check is
a literal filename match, so any other name, capitalization, or extension
will silently 404 instead of showing the PDF):

```
public/TweetDelete for Linux.pdf
public/TweetDelete for Debian.pdf
public/TweetDelete for Ubuntu.pdf
```

The web app's footer link (`/api/help`) automatically finds whichever
platform's PDF is present in the build, so no code changes are needed —
just add the file before running `build_deb.sh`. As with Windows, this
must go in the **source** `public/` folder, not anywhere under a build
output directory, since those get regenerated on every build.

## Building the package

No separate dependency-install step is needed here — `dpkg-deb`,
`fakeroot`, and Python's standard library are all this build script uses,
and are either already on Ubuntu or a single `apt install` away:

```bash
sudo apt install -y dpkg-dev fakeroot
```

Then, from the project root:

```bash
bash packaging/linux/build_deb.sh
```

This produces `packaging/linux/output/tweetdelete_1.0.0_all.deb`.
`fakeroot` is what lets the files inside the package be owned by
`root:root` without this build script itself needing to run as root.

To double-check what actually went into it before installing:

```bash
dpkg -I packaging/linux/output/tweetdelete_1.0.0_all.deb   # metadata
dpkg -c packaging/linux/output/tweetdelete_1.0.0_all.deb   # file listing
```

If you have `lintian` installed (`sudo apt install lintian`), it's worth
running against the build too — this package intentionally accepts two
categories of lintian warning as a fair tradeoff for staying lightweight
rather than adopting the full `debhelper`/`dh_*` toolchain:

- `maintainer-script-calls-systemctl` — full Debian policy prefers routing
  systemd enable/disable through `dh_installsystemduser`'s generated
  helpers rather than calling `systemctl` directly in `postinst`/`prerm`.
  Since this package hard-depends on `systemd` and isn't meant for
  wide distribution, calling it directly is a reasonable simplification.
- `no-manual-page` for `/usr/bin/tweetdelete` — a nice-to-have for public
  packages, not worth it for a personal tool.

## Installing it

```bash
sudo apt install ./packaging/linux/output/tweetdelete_1.0.0_all.deb
```

(Using `apt install ./file.deb` rather than `dpkg -i` is preferred since
apt will also pull in `python3`, `xdg-utils`, and `systemd` automatically
if any are somehow missing.)

`postinst` runs `systemctl --global enable tweetdelete.service`, which
tells systemd to start it for **every user, at every future login** —
this is the correct systemd mechanism for exactly this situation, not a
workaround (verified against systemd's own documentation). It also makes a
best-effort attempt to start it immediately for whoever ran the install
command, so it's usable right away without needing to log out and back in
first. That immediate-start step depends on `sudo` correctly passing along
who you are (which it does in the normal case of running `sudo apt
install ./file.deb` from your own logged-in desktop session) — if it
doesn't fire for any reason, nothing is broken; it will simply start at
your next login regardless, or you can start it yourself right away:

```bash
systemctl --user start tweetdelete.service
```

Once running, launch the app from the Activities/Applications menu (search
"TweetDelete"), or from a terminal:

```bash
tweetdelete
```

Both just open your default browser to the running service — there's no
tray icon on Linux (stock GNOME removed native tray icon support entirely,
so this would be inconsistent across desktop environments; a terminal
command is more reliable here).

## Checking on it / logs

```bash
systemctl --user status tweetdelete.service
journalctl --user -u tweetdelete.service -f
```

## Stopping it

You decided there's no strong reason to stop it in general, so there's no
in-app control for this — just the standard systemd commands, which are
also worth noting in the help PDF:

```bash
systemctl --user stop tweetdelete.service       # stop it for this session
systemctl --user disable --now tweetdelete.service   # stop it and don't
                                                        # auto-start at login
```

## Uninstalling

```bash
sudo apt remove tweetdelete
```

`prerm` stops the service for the invoking user and runs `systemctl
--global disable` before files are removed — tested end-to-end in this
build environment: install, verify the service registers and the app
runs correctly from its installed location, then remove and confirm every
file, the icon, the desktop entry, and the global-enable symlink are all
cleanly gone.

## What's different from the Windows build, at a glance

| | Windows | Linux |
|---|---|---|
| Python | Bundled (PyInstaller) | System `python3` via `apt Depends:` |
| Background process | Tray icon (Open/Quit) | `systemd --user` service |
| Auto-start | No (launch on demand) | Yes, at every login |
| Stop control | Tray "Quit" | Terminal command only |
| Launcher | Start Menu / desktop shortcut | Applications menu / `tweetdelete` command |
| Install scope | Per-user, no admin prompt | System-wide via `apt`, needs `sudo` |

## Sources

- systemd `--global enable` mechanism: https://wiki.archlinux.org/title/Systemd/User
- GNOME dropped native tray icon (AppIndicator) support: well-documented, widely-cited desktop environment change
