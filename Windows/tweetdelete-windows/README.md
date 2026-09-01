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
- Warns you if you try to close the tab mid-run.
- Remembers your last-selected categories, delete option, and custom dates
  as the default the next time you open the tool.
- Paces each category independently against X's real limits: 50 deletions
  per 15 minutes for posts, 50/15min for undoing reposts, and 50/15min plus
  1,000/24hr for unliking — and backs off automatically on rate-limit
  responses.

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
with a server component. `server.py` does nothing except:

1. Serve the static files in `public/` (the actual app).
2. Forward `/api/x/...` requests to `https://api.x.com/...` and relay the
   response back — this makes the browser's calls same-origin, so CORS
   never applies.
3. Host `callback.html` at `http://127.0.0.1:<port>/callback.html`, which X
   requires for the OAuth redirect (it does not allow `file://` URLs).

No business logic, filtering, or credentials live in this script. It never
sees your OAuth client secret, because public/SPA clients don't have one.

## Windows installer (no Python required for end users)

If you'd rather have a normal double-click-to-install Windows app — Start
Menu entry, desktop icon, tray icon, browser opens automatically — see
[PACKAGING.md](./PACKAGING.md). It walks through building a PyInstaller +
Inno Setup installer that bundles its own Python interpreter, so it never
depends on (or conflicts with) any Python already on the machine.

This is the Windows-focused source tree. There are separate Linux and
Android source trees that share the same core `server.py` / `public/`
code with their own OS-specific packaging. The sections below describe
running this tree the plain way, directly from this source folder.

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
server.py           local helper: static file server + CORS-avoiding proxy
public/
  index.html        app shell / all screens
  style.css         styling, responsive layout
  app.js            UI logic, delete orchestration, pacing, persistence
  oauth.js          PKCE login, token storage/refresh
  api.js            X API v2 calls (account info, list/delete posts, undo reposts, unlike)
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
