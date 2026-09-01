// api.js — thin wrapper around the X API v2 endpoints this app needs.
// All calls go through /api/x/... (the local proxy in server.py) because
// api.x.com does not support CORS for direct browser requests.

import { getValidAccessToken } from "./oauth.js";

const BASE = "/api/x";

async function authedFetch(path, options = {}) {
  const token = await getValidAccessToken();
  const headers = Object.assign({}, options.headers, {
    Authorization: `Bearer ${token}`,
  });
  return fetch(BASE + path, Object.assign({}, options, { headers }));
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Calls fn(), and if the response is a 429, waits until x-rate-limit-reset
// (plus a small buffer) and retries, up to maxRetries times. onWait is an
// optional callback(waitSeconds) for progress UI.
async function withRateLimitRetry(fn, { maxRetries = 6, onWait = null } = {}) {
  let attempt = 0;
  while (true) {
    const res = await fn();
    if (res.status !== 429) return res;
    attempt++;
    if (attempt > maxRetries) return res;

    const resetHeader = res.headers.get("x-rate-limit-reset");
    let waitMs = 15000;
    if (resetHeader) {
      const resetAt = parseInt(resetHeader, 10) * 1000;
      waitMs = Math.max(resetAt - Date.now(), 1000) + 2000;
    }
    if (onWait) onWait(Math.round(waitMs / 1000));
    await sleep(waitMs);
  }
}

// Returns { id, username, name, public_metrics: { tweet_count, like_count, ... } }.
// public_metrics.tweet_count is used to detect the 3,200-post API cap;
// public_metrics.like_count is used the same way for liked posts. Both are
// exact lifetime totals from X itself, so this works regardless of what
// any current or future API limit happens to be.
export async function getMe() {
  const res = await authedFetch("/2/users/me?user.fields=public_metrics");
  if (!res.ok) throw new Error(`Failed to fetch account info (${res.status})`);
  const data = await res.json();
  return data.data;
}

// Fetches as much of the user's post history as the API will return
// (paginating 100 at a time), including posts, replies, quote posts, and
// reposts/retweets all mixed together — app.js splits them apart by
// referenced_tweets. Calls onPage(newItems, totalSoFar, meta) after each
// page. Stops after a defensive hard ceiling to avoid a runaway loop.
//
// Known limitation (X's API, not this app): this endpoint has historically
// capped out at a user's most recent 3,200 posts, but the exact number
// isn't contractually documented and could change. Rather than hardcode
// 3200, app.js compares the count this function returns against the
// account's true lifetime public_metrics.tweet_count to detect definitively
// whether anything was left out — whatever that limit is, now or later.
export async function fetchAllPosts(userId, { onPage = null, shouldStop = null } = {}) {
  const results = [];
  let paginationToken = null;
  const HARD_CEILING = 20000;

  do {
    if (shouldStop && shouldStop()) break;

    const params = new URLSearchParams({
      max_results: "100",
      "tweet.fields": "created_at,referenced_tweets",
    });
    if (paginationToken) params.set("pagination_token", paginationToken);

    const res = await withRateLimitRetry(
      () => authedFetch(`/2/users/${userId}/tweets?${params.toString()}`),
      { onWait: (s) => onPage && onPage([], results.length, { rateLimitedSeconds: s }) }
    );

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Failed to fetch posts (${res.status}): ${text}`);
    }

    const data = await res.json();
    const page = data.data || [];
    results.push(...page);
    if (onPage) onPage(page, results.length, {});

    paginationToken = data.meta && data.meta.next_token ? data.meta.next_token : null;
  } while (paginationToken && results.length < HARD_CEILING);

  return results;
}

// Fetches as much of the user's liked-posts history as the API will
// return. Same cap-detection approach as fetchAllPosts, compared against
// public_metrics.like_count.
export async function fetchAllLikedPosts(userId, { onPage = null, shouldStop = null } = {}) {
  const results = [];
  let paginationToken = null;
  const HARD_CEILING = 20000;

  do {
    if (shouldStop && shouldStop()) break;

    const params = new URLSearchParams({
      max_results: "100",
      "tweet.fields": "created_at",
    });
    if (paginationToken) params.set("pagination_token", paginationToken);

    const res = await withRateLimitRetry(
      () => authedFetch(`/2/users/${userId}/liked_tweets?${params.toString()}`),
      { onWait: (s) => onPage && onPage([], results.length, { rateLimitedSeconds: s }) }
    );

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Failed to fetch likes (${res.status}): ${text}`);
    }

    const data = await res.json();
    const page = data.data || [];
    results.push(...page);
    if (onPage) onPage(page, results.length, {});

    paginationToken = data.meta && data.meta.next_token ? data.meta.next_token : null;
  } while (paginationToken && results.length < HARD_CEILING);

  return results;
}

// Deletes a single authored post/reply by ID.
export async function deletePost(id) {
  const res = await withRateLimitRetry(() =>
    authedFetch(`/2/tweets/${id}`, { method: "DELETE" })
  );
  if (res.status === 429) return { ok: false, status: 429, rateLimited: true };
  if (!res.ok) {
    let detail = "";
    try {
      detail = await res.text();
    } catch {}
    return { ok: false, status: res.status, detail };
  }
  return { ok: true, status: res.status };
}

// Undoes a repost/retweet. sourceTweetId is the ORIGINAL post's ID (not the
// ID of the repost activity itself).
export async function undoRepost(userId, sourceTweetId) {
  const res = await withRateLimitRetry(() =>
    authedFetch(`/2/users/${userId}/retweets/${sourceTweetId}`, { method: "DELETE" })
  );
  if (res.status === 429) return { ok: false, status: 429, rateLimited: true };
  if (!res.ok) {
    let detail = "";
    try {
      detail = await res.text();
    } catch {}
    return { ok: false, status: res.status, detail };
  }
  return { ok: true, status: res.status };
}

// Removes a like from a post.
export async function undoLike(userId, tweetId) {
  const res = await withRateLimitRetry(() =>
    authedFetch(`/2/users/${userId}/likes/${tweetId}`, { method: "DELETE" })
  );
  if (res.status === 429) return { ok: false, status: 429, rateLimited: true };
  if (!res.ok) {
    let detail = "";
    try {
      detail = await res.text();
    } catch {}
    return { ok: false, status: res.status, detail };
  }
  return { ok: true, status: res.status };
}

export async function revokeToken(token, clientId) {
  const body = new URLSearchParams({ token, client_id: clientId, token_type_hint: "refresh_token" });
  try {
    await fetch(`${BASE}/2/oauth2/revoke`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    });
  } catch {
    // Best-effort only; local session is cleared regardless.
  }
}
