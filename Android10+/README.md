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
- **`DeletionEngine.kt`** (v1.0.3+) — the actual delete loop, in Kotlin,
  running inside `ProxyService`. The web UI still handles login, fetching,
  filtering and the archive step, then hands the final target list to this
  engine via the JS bridge and only displays its progress. See "Background
  runs" below for why.
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

`keystore/tweetdelete-release.keystore` is a throwaway signing key
generated for this build (alias `tweetdelete`, password `tweetdelete`,
overridable via `TD_KEYSTORE_PASSWORD`/`TD_KEY_PASSWORD` env vars) so
`assembleRelease` works out of the box. **Treat it as sensitive and back
it up** — Android requires every update to an app be signed with the
same key, so losing it means future versions can't overwrite this one
(users would have to uninstall and reinstall). Before distributing this
beyond your own devices, regenerate it with your own strong, private
password:

```bash
keytool -genkeypair -v -keystore keystore/tweetdelete-release.keystore \
  -alias tweetdelete -keyalg RSA -keysize 2048 -validity 10000
```

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

## Background runs (fixed in v1.0.3)

Up to v1.0.2 the deletion loop was JavaScript inside the WebView. The
foreground service and wake lock kept the app process alive, but not the
WebView's JavaScript timers: once the page is hidden (app in background or
screen off) Chromium throttles and can suspend them. The 15-minute
rate-limit wait also counted 500 ms ticks instead of checking the clock, so
under throttling it effectively never ended — runs stalled after the first
50 deletions. Battery-optimisation settings could not help, because the
process was never the thing being stopped.

From v1.0.3:

- The loop runs natively in the foreground service (`DeletionEngine.kt`),
  independent of the WebView. All waits are wall-clock based.
- Rate limits: pre-emptive 50 per 15 min per category (plus 1,000 per 24 h
  for likes), and X's own `x-rate-limit-remaining` / `x-rate-limit-reset`
  headers are honoured; a 429 retries the same item after the reset time
  instead of recording a failure.
- Network drop-outs retry the same item with back-off (15 s up to 5 min)
  instead of aborting the whole run.
- Access tokens (2-hour lifetime) are refreshed natively; the rotated
  refresh token is handed back to the page so you stay logged in after.
- Run state is saved after every item to app-private storage. If Android
  kills the process anyway, the run resumes when the service is recreated
  (sticky restart, or simply reopening the app).
- 10 consecutive failures with the same HTTP status (e.g. exhausted API
  credits) auto-pause the run instead of burning through the list.
- The notification shows progress and the time the current rate-limit
  wait ends.
- The wake lock is renewed continuously in 1-hour slices (the old fixed
  12-hour cap was shorter than a full 3,200-post run).

### Countdown display (v1.0.4)

Rate-limit waits show a live `m:ss` countdown (`h:mm:ss` for waits of an
hour or more) plus the clock time of resumption, in the app while it is on
screen and in the notification (Android's own chronometer, so it ticks
without waking the app). This applies both while fetching posts and while
deleting. If X still refuses once the countdown ends, the app shows
"Waiting for X to accept resumption", with a new countdown if X gives a
later reset time.

For best results on GrapheneOS/stock Android, leave battery usage for
TweetDelete on "Unrestricted". That also allows Android 12+ to restart the
service from the background after a kill.

## Known limitations (carried over from the desktop build, still apply)

- X's pay-per-use API pricing applies here exactly as on desktop — reading
  a large post history costs real money before you delete anything.
- A foreground service materially improves survivability but is not a
  guarantee against a reboot, a manual force-stop, or severe memory
  pressure killing the process mid-run. From v1.0.3 an interrupted run
  resumes from its saved position; a fresh run also re-fetches the live
  remaining set from the API, so starting again is always safe.
- Rebooting or force-stopping the app stops the run until the app is
  opened again (it then resumes automatically).

## Sources

- X app types and disallowed callback URL schemes: https://docs.x.com/fundamentals/developer-apps
- X OAuth 2.0 PKCE flow: https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code
- RFC 8252 (OAuth for native apps, loopback redirect pattern): https://datatracker.ietf.org/doc/html/rfc8252
- GrapheneOS WebView (Vanadium): https://grapheneos.org/usage
- LineageOS WebView updates: https://lineageos.org/Changelog-28/
