# TweetDelete

A browser-based tool to bulk delete your own posts and replies on X, filtered
by date range. All logic (filtering, pacing, progress, confirmation) runs as
plain JavaScript in your browser. A small local Python script is required
only because X's API does not support CORS and does not allow `file://`
OAuth redirects — see **Why the local script?** below.

## What it does

- Connects to your own X account via OAuth 2.0 (Authorization Code + PKCE) —
  no password is ever entered into this app.
- Lets you pick any combination of three categories: **posts & replies**,
  **reposts (retweets)**, and **likes**.
- Lets you choose what to delete: **Everything**, **older than 7 days**,
  **older than 30 days**, or a **custom date range** — applied across all
  selected categories. For likes, the date used is the original post's date
  (X doesn't expose *when you liked* something, either via API or archive).
- Always tries the live API first. Before deleting anything, it compares
  the number of posts/likes the API actually returned against your
  account's true lifetime totals (`public_metrics.tweet_count` /
  `like_count`, taken straight from your own profile) — an exact,
  future-proof check rather than a hardcoded number. If there's a gap, it
  asks you to optionally upload the matching file(s) from your X data
  archive to fill it in; if you skip that step, it proceeds with only what
  the API found.
- Shows a live count and progress bar, with **Pause / Continue / Cancel**
  controls, before anything is deleted.
- (v1.0.4) Runs the deletions in the local helper, not the browser tab, so
  you can minimise or close the window mid-run — see **Background runs**
  below. Shows a live `m:ss` countdown (`h:mm:ss` for waits over an hour)
  to the next batch, with the clock time it resumes.
- (v1.0.4) Opens as a compact app window sized to the app — see
  **App window** below.
- Remembers your last-selected categories, delete option, and custom dates
  as the default the next time you open the tool.
- Paces each category independently against X's real limits: 50 deletions
  per 15 minutes for posts, 50/15min for undoing reposts, and 50/15min plus
  1,000/24hr for unliking — and also honours X's own rate-limit headers, so
  if X allows fewer than that it waits for X's reset time instead of
  recording failures.

## Background runs (v1.0.4)

Up to v1.0.0 the delete loop was JavaScript in the browser tab. Browsers
throttle or freeze timers in hidden tabs (Chrome/Edge cut them to once a
minute after 5 minutes hidden; Edge "sleeping tabs" and Chrome "Memory
Saver" freeze the tab completely), and the 15-minute wait counted timer
ticks rather than checking the clock. So whether a run got past the first
50 depended on the browser and its settings — the Windows and Linux builds
shipped identical code.

From v1.0.4, the same design as Android v1.0.3+:

- The page still handles login, fetching, filtering, the archive step and
  confirmation, then hands the final list to `runner.py` inside the local
  helper (the tray app on Windows, the systemd user service on Linux) and
  only displays its progress.
- All waits are wall-clock based. A 429 retries the same item after X's
  reset time. If X still refuses once a countdown ends, the status reads
  "Waiting for X to accept resumption", with a new countdown if X gives a
  later reset.
- Network drop-outs retry with back-off (15 s up to 5 min) instead of
  aborting. Access tokens are refreshed by the runner, and the rotated
  refresh token is handed back to the page.
- 10 consecutive failures with the same HTTP status (e.g. exhausted API
  credits) auto-pause the run.
- Run state is saved after every item to `%LOCALAPPDATA%\TweetDelete\run_state.json`
  (Windows) or `~/.local/state/tweetdelete/run_state.json` (Linux, mode
  0600 — it holds your tokens while a run is active, and is deleted when
  the finished run is shown). If the helper stops (tray Quit, logout,
  reboot), the run resumes from where it left off when the helper next
  starts — on Linux automatically at login, on Windows when you next launch
  TweetDelete.
- On Windows the tray icon tooltip shows progress and the time of the
  next batch.
- The runner endpoints (`/__runner/...`) accept requests only from the app
  itself: no CORS, a Host-header check against DNS rebinding, and a
  required custom header.

## App window (v1.0.4)

TweetDelete now opens in a standalone app window (no tabs or address bar)
sized to the app, rather than a tab in a full-size browser window. This
uses your Chromium-family browser's `--app` mode (Edge, Chrome, Chromium,
Brave or Vivaldi) with your normal browser profile, so saved settings and
X login in that browser are kept; the page then fits the window to the
app in CSS pixels so it is right under any display scaling, and centres
it. The fit happens once per window, so if you resize it by hand it stays
that way.

Browser choice: your default browser if it is Chromium-family, otherwise
any installed one (Edge first on Windows), otherwise a plain tab in your
default browser as before (Firefox has no app-window mode). To force a
browser, or the old behaviour, create `launcher.json` in
`%LOCALAPPDATA%\TweetDelete\` (Windows) or `~/.config/tweetdelete/`
(Linux):

```json
{ "browser": "default" }
```

or `{ "browser": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" }`.
Optional `"width"` / `"height"` set the starting size. On Linux the
`TWEETDELETE_BROWSER` environment variable works too.

If you switch browser (e.g. from Firefox to Edge), that browser's storage
is separate, so you'll be asked for your Client ID and to connect to X once
more.

## Browser cache revalidation (v1.0.4.3)

The desktop helper sends `Cache-Control: no-cache` for HTML, JavaScript
and CSS, including the root page and conditional `304 Not Modified`
responses. Browsers may store these files, but must validate them with the
helper before reuse. Existing `Last-Modified` checks let unchanged files
return 304 without downloading them again; modified files are sent afresh.
The health and runner JSON responses retain their `no-store` policy.

Install the new build and restart the helper to activate this policy.
Previously cached responses do not acquire new headers retroactively:
one final hard refresh may be needed when upgrading from an older build.
This change does not replace an old helper process that is still running.

## Version reporting (v1.0.4.2)

The app now shows its version in the web UI footer ("TweetDelete
v1.0.4.2"), served by the local helper's `/__tweetdelete_health` endpoint —
so you can always see which build is actually running, and a stale install
(an update that never landed) is visible at a glance instead of needing
`dpkg` or the Windows Apps list to check.

The version lives in one place only: `VERSION` at the top of `server.py`.
The Windows .exe's version resource (file Properties, and the version
shown in Apps & Features), the Inno Setup installer's version number and
output filename, and the Debian package's metadata and filename all read
from it at build time. Before, each of those carried its own hardcoded
string, which is how a build could end up labelled one version while
containing another's code. The Android app is unchanged: its local server
has no health endpoint, so the footer line simply stays empty there.

## Known limitation (X's API, not this tool)

`GET /2/users/:id/tweets` and `GET /2/users/:id/liked_tweets` have
historically capped out well short of a very active account's full history
(commonly cited as ~3,200 for posts), and the exact number isn't
contractually documented — it could change without notice
(https://docs.x.com/enterprise-api/posts/timelines/integrate). Rather than
hardcode that number, this tool compares what it actually fetched against
your account's real lifetime totals and only asks for your archive when
there's a genuine, measured gap — so it keeps working correctly if X raises
or lowers the limit in the future.

One archive caveat worth knowing: X's personal data export uses an older,
legacy tweet format. Structured retweets (where the export includes a
nested `retweeted_status` with the original post's ID) can be undone
normally. Old-style manual "RT @username: ..." text posts have no such
reference — there's no reliable original-post ID to call the undo-repost
API with, so the tool skips those with a note rather than guessing.
Likewise, `like.js` never records *when* you liked something (only the
liked post's own text/ID) — this tool derives the post's original date from
its ID (X's IDs encode a creation timestamp), the same value the live API
would show for that post, so date filtering behaves consistently whether a
liked post came from the API or your archive.

## Cost — this is not free

Since February 2026, X's API uses pay-per-use billing for new developer
accounts: **$0.005 per post read** and **$0.015 per post write** (delete
pricing is not separately published, so assume it falls under one of these
two). No free tier exists for new developer accounts. Reading a large post
history before deleting anything will cost real money — e.g. scanning 3,200
posts costs roughly $16 in reads alone at 100 posts/request. Set up billing
in your [X Developer Console](https://console.x.com) before using this tool.
There is no way for this app to avoid that cost; it is an X API policy, not
a limitation of the code.

## Security note — persisted login

Per your instruction, this build stores a refresh token in your browser's
local storage (`offline.access` scope) so you don't have to log in every
session. That means **anyone with access to this browser profile can act as
you on X** for as long as the token is valid. Use the **Disconnect** button
to revoke and erase it when you're done, especially on a shared machine.

## Why the local script?

X's API does not send `Access-Control-Allow-Origin` headers on any
endpoint, so a browser calling `api.x.com` directly is blocked by CORS —
this has been an open, unresolved limitation since at least 2016
(https://devcommunity.x.com/t/twitter-api-v2-public-client-no-access-control-allow-origin-header-present-cors/170402).
Every tool that bulk-deletes tweets, including this one, works around it
with a server component. `server.py` does three things:

1. Serve the static files in `public/` (the actual app).
2. Forward `/api/x/...` requests to `https://api.x.com/...` and relay the
   response back — this makes the browser's calls same-origin, so CORS
   never applies.
3. Host `callback.html` at `http://127.0.0.1:<port>/callback.html`, which X
   requires for the OAuth redirect (it does not allow `file://` URLs).

4. (v1.0.4) Host the background deletion runner (`runner.py`), which
   holds your access/refresh tokens only for the duration of a run.

Filtering and selection stay in the browser. Public/SPA clients have no
client secret, so none is ever used or stored.

## Windows installer (no Python required for end users)

If you'd rather have a normal double-click-to-install Windows app — Start
Menu entry, desktop icon, tray icon, browser opens automatically — see
[PACKAGING.md](./PACKAGING.md). It walks through building a PyInstaller +
Inno Setup installer that bundles its own Python interpreter, so it never
depends on (or conflicts with) any Python already on the machine.

## Ubuntu / Debian package

For a normal `apt`-installed app on Ubuntu — Applications-menu entry,
background service that starts automatically at login — see
[DEBIAN_PACKAGING.md](./DEBIAN_PACKAGING.md). Unlike Windows, this does
**not** bundle Python — it depends on Ubuntu's own `python3` via `apt`,
since the server has no third-party dependencies and Ubuntu already
guarantees a compatible interpreter is present.

The sections below describe running it the plain way instead, directly
from this source folder, on any platform.

## Setup

### 1. Requirements

- Python 3.8 or later (check with `python3 --version`). No other
  dependencies — the script uses only Python's standard library.
- Any modern-ish browser (Chrome, Firefox, Edge, Safari — going back several
  versions works fine; the app avoids anything newer than widely-supported
  JavaScript).

### 2. Configure your X Developer App

You said you already have a Project/App set up. Confirm these settings
under your app's **User authentication settings**:

| Setting | Value |
|---|---|
| OAuth 2.0 | Enabled |
| App type | **Public client / Single Page App** (not "Web App" / confidential) |
| Callback / redirect URI | `http://127.0.0.1:8765/callback.html` (must match exactly, including port — see below if you use a different port) |
| Scopes | `tweet.read` `tweet.write` `like.read` `like.write` `users.read` `offline.access` |

Copy the **Client ID** (not the secret — public clients don't need one, and
this app never uses one) from Keys and tokens.

### 3. Run the local server

```bash
cd tweetdelete
python3 server.py
```

You'll see:

```
TweetDelete running at http://127.0.0.1:8765/
OAuth callback / redirect URI to register in your X app: http://127.0.0.1:8765/callback.html
```

Open `http://127.0.0.1:8765/` in your browser.

If port 8765 is already in use, run `python3 server.py 9000` (or any free
port) and register the matching callback URI in your X app instead.

### 4. First use

1. Enter your Client ID and the redirect URI (defaults to
   `http://127.0.0.1:8765/callback.html` — only change this if you used a
   different port). Click Save.
2. Click **Connect to X**, approve access on X, and you'll be returned here.
3. Pick which categories to include (posts & replies, reposts, likes — any
   combination) and a date range, then click **Delete**.
4. If the API can't return your complete history for a selected category,
   you'll be prompted to optionally upload your X data archive (tweet.js /
   like.js) to fill the gap — or just click Continue to proceed with what
   the API found.
5. Review the count and date range shown, then click **Start deleting** to
   confirm, or **Cancel** to back out without deleting anything.

## Files

```
server.py           local helper: static file server + CORS-avoiding proxy + runner endpoints
runner.py           background deletion runner (port of Android's DeletionEngine.kt)
launch_window.py    opens the compact app window (Chromium-family --app), else default browser
tests/              runner tests against a mock X API: python3 -m unittest discover -s tests
public/
  index.html        app shell / all screens
  style.css         styling, responsive layout
  app.js            UI logic, delete orchestration, pacing, persistence
  oauth.js          PKCE login, token storage/refresh
  api.js            X API v2 calls (account info, list/delete posts, undo reposts, unlike)
  runner.js         page-side interface to the background runner (desktop helper or Android)
  archive.js        Optional X data archive (tweet.js / like.js) parser, used only on a detected gap
  callback.html     OAuth redirect landing page
```

## Reconnecting after this update

This version requests two additional OAuth scopes (`like.read`, `like.write`)
needed for the likes feature. Your previously-stored login doesn't have
them — click **Disconnect**, then **Connect to X** again to re-authorize
with the new scopes. If you skip this, likes-related requests will fail
with a 403/insufficient-scope error while posts and reposts continue to
work normally.

## Sources

- CORS not supported by X's API: https://devcommunity.x.com/t/twitter-api-v2-public-client-no-access-control-allow-origin-header-present-cors/170402
- OAuth 2.0 PKCE for public clients and scopes: https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code
- Rate limits (posts/reposts/likes, all 50/15min; likes also 1,000/24hr): https://docs.x.com/x-api/fundamentals/rate-limits
- Historical post/like retrieval caps: https://docs.x.com/enterprise-api/posts/timelines/integrate
- User `public_metrics` fields (`tweet_count`, `like_count`): https://docs.x.com/x-api/fundamentals/data-dictionary
- Undo a repost endpoint: https://docs.x.com/x-api/posts/retweets/introduction
- Likes endpoints: https://docs.x.com/x-api/posts/likes/introduction
- 2026 pay-per-use pricing: https://postproxy.dev/blog/x-api-pricing-2026/
