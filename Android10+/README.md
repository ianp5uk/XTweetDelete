# TweetDelete for Android

A native Android wrapper around the same TweetDelete web app used on
desktop (`public/` — HTML/CSS/JS, adapted here for mobile screens). The
deletion logic, filtering, pacing, and X API calls are the same JavaScript
as the desktop build; only the plumbing around it is native Android.

## How this differs from the desktop build

The desktop version needs a local Python script (`server.py`) for two
reasons: X's API has no CORS support, and X's OAuth redirect can't be
`file://`. Android has no equivalent to that script, so this app replaces
it with:

- **`ProxyServer.kt`** — a tiny embedded HTTP server (NanoHTTPD + OkHttp)
  bound to `127.0.0.1` inside the app, doing the same two jobs as
  `server.py`: serving the bundled web UI from the APK's assets, and
  reverse-proxying `/api/x/...` to `https://api.x.com/...`.
- **`ProxyService.kt`** — a foreground service that owns that server and
  shows a persistent notification while a deletion run is active, plus a
  wake lock so the CPU doesn't sleep mid-run. X paces deletions to 50 every
  15 minutes, so a large account can take hours; Android suspends
  background work far more aggressively than a desktop browser tab, and
  this is the standard way to ask it not to.
- **`MainActivity.kt`** — hosts a `WebView` pointed at
  `http://127.0.0.1:<port>/`, wires up the Android file picker for the
  optional X-archive upload (`archive.js`'s `<input type="file">`), and
  exposes a small JS bridge (`window.AndroidBridge`) that `app.js` calls to
  toggle the foreground notification and save the CSV deletion log
  straight to the Downloads folder (blob-URL downloads don't work in a
  WebView the way they do in a real browser tab).

Everything else — OAuth 2.0 PKCE, the X API calls, filtering, the archive
parser, the UI — is the unmodified web app, with only `style.css` getting
extra breakpoints for narrow phones, safe-area insets (notches/gesture
bars), and short landscape, and `index.html`/`app.js` getting the small,
clearly-commented hooks described above.

## Setting up your X Developer App for Android

Register a **second callback URL** on the *same* X app you already use for
desktop (Client ID can be shared), or a separate app if you'd rather keep
them apart:

1. developer.x.com → your app → **User authentication settings** → App
   type must be **Native App** (not Web App, not Single Page App).
2. Add callback URL `http://127.0.0.1:8765/callback.html` — the app tries
   this exact port first, matching the desktop default, so if you've
   already registered it there, Android needs no new registration at all.
   Custom URL schemes (e.g. `tweetdelete://callback`) are **not accepted**
   by X for this flow — see `docs.x.com/fundamentals/developer-apps`.
3. Scopes: `tweet.read tweet.write like.read like.write users.read
   offline.access` (same as desktop).
4. In the app's first-run screen, enter the Client ID; leave the redirect
   URI on its default unless port 8765 was busy on your phone, in which
   case check the in-app value (it auto-fills whatever port the embedded
   server actually bound to) and register that instead.

## Building the APK

```bash
cd android/TweetDelete
./gradlew assembleDebug      # app/build/outputs/apk/debug/app-debug.apk
./gradlew assembleRelease    # app/build/outputs/apk/release/app-release.apk
```

Requires JDK 17 and the Android SDK (`ANDROID_HOME`/`local.properties`
pointing at platform 34 + build-tools 34.0.0); `./gradlew` downloads
Gradle itself on first run.

### Signing key

This source tree does **not** include a keystore — a signing key is
private and should never be committed to a public repo, even a
self-generated one. `app/build.gradle.kts`'s `release` signing config
points at `../keystore/tweetdelete-release.keystore`, which you need to
generate yourself once before `assembleRelease` will work:

```bash
mkdir -p keystore
keytool -genkeypair -v -keystore keystore/tweetdelete-release.keystore \
  -alias tweetdelete -keyalg RSA -keysize 2048 -validity 10000
```

Set `TD_KEYSTORE_PASSWORD`/`TD_KEY_PASSWORD` env vars if you don't want
to be prompted interactively, then **back the keystore file up
somewhere private** — Android requires every update to an app be signed
with the same key, so losing it means future versions can't overwrite
this install (users would have to uninstall and reinstall). `.gitignore`
in this tree already excludes `/keystore/` so you don't accidentally
commit it.

## Installing the APK

No Play Store involved. On the target device: Settings → allow installing
apps from the source you're using (browser/file manager/ADB), then open
the APK, or `adb install app-release.apk`.

## Compatibility notes

- **minSdk 29 (Android 10, 2019) / targetSdk 34.** Covers essentially all
  actively-used devices, current GrapheneOS, and current LineageOS.
- **GrapheneOS**: uses its own Vanadium WebView (kept current) as the
  system WebView provider — no special handling needed, it's a drop-in
  replacement from this app's point of view.
- **LineageOS**: ships and updates its own Chromium-based WebView per
  release. Only a very old or unmaintained ROM build would have a WebView
  old enough to matter for the modern CSS used here (`color-mix()`,
  `:has()`) — both degrade gracefully (a missed color tint, a missed
  "checked" highlight) rather than breaking anything.
- No Google Play Services / Google-only APIs are used anywhere, so none of
  this depends on Play Services being present.

## What hasn't been verified

This sandbox has no Android emulator or physical device attached (no
`/dev/kvm`, no display), so the build has been verified by: a clean
Gradle build of both debug and release variants, `apksigner verify`
confirming a valid signature, and a manual review of every code path
(proxy, OAuth loopback, file chooser, CSV export, foreground service).
It has **not** been run on an actual device yet. Before relying on it,
install the release APK on a real phone (or an Android Studio emulator on
your own machine) and walk through: connecting to X, running a small test
deletion, backgrounding the app mid-run to confirm the notification keeps
it alive, and the CSV log download.

## Known limitations (carried over from the desktop build, still apply)

- X's pay-per-use API pricing applies here exactly as on desktop — reading
  a large post history costs real money before you delete anything.
- A foreground service materially improves survivability but is not a
  guarantee against a reboot, a manual force-stop, or severe memory
  pressure killing the process mid-run. The deletion loop re-fetches the
  live remaining set from the API on every run rather than trusting a
  saved checkpoint, so simply reopening the app and starting again safely
  picks up wherever an interrupted run left off.

## Sources

- X app types and disallowed callback URL schemes: https://docs.x.com/fundamentals/developer-apps
- X OAuth 2.0 PKCE flow: https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code
- RFC 8252 (OAuth for native apps, loopback redirect pattern): https://datatracker.ietf.org/doc/html/rfc8252
- GrapheneOS WebView (Vanadium): https://grapheneos.org/usage
- LineageOS WebView updates: https://lineageos.org/Changelog-28/
