// app.js — UI controller for TweetDelete. Vanilla JS, no build step, no
// framework, so the tool keeps working in older browsers and is trivially
// portable. All state lives in the browser (localStorage / in-memory);
// the only network calls are to X's API, proxied through server.py.

import * as oauth from "./oauth.js";
import * as api from "./api.js";
import * as archive from "./archive.js";

const PREFS_KEY = "td_prefs";

const el = (id) => document.getElementById(id);

const screens = {
  settings: el("screenSettings"),
  connect: el("screenConnect"),
  main: el("screenMain"),
  archive: el("screenArchive"),
  progress: el("screenProgress"),
  done: el("screenDone"),
};

function showScreen(name) {
  Object.values(screens).forEach((s) => s.classList.add("hidden"));
  screens[name].classList.remove("hidden");
}

const PREFS_DEFAULTS = { categories: ["posts"], mode: "everything", customStart: "", customEnd: "" };

function loadPrefs() {
  // Defensive against stale localStorage from an earlier version of this
  // app that predates the categories field (posts-only, no reposts/likes
  // selection) - merge onto defaults rather than trusting the stored
  // shape wholesale, so an old or partial object never crashes the UI.
  try {
    const stored = JSON.parse(localStorage.getItem(PREFS_KEY) || "null");
    if (!stored || typeof stored !== "object") return { ...PREFS_DEFAULTS };
    return {
      ...PREFS_DEFAULTS,
      ...stored,
      categories: Array.isArray(stored.categories) ? stored.categories : PREFS_DEFAULTS.categories,
    };
  } catch {
    return { ...PREFS_DEFAULTS };
  }
}

function savePrefs(prefs) {
  localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
}

// A small sliding-window rate limiter that can track more than one window
// at once (X's likes-delete endpoint has both a 50/15min and a 1,000/24hr
// cap, for example). Pre-emptively waits for a free slot rather than
// relying only on 429 recovery, to keep runs efficient.
class RateLimiter {
  constructor(windows) {
    this.windows = windows.map((w) => ({ ...w, timestamps: [] }));
  }

  msUntilFree() {
    const now = Date.now();
    let waitMs = 0;
    for (const w of this.windows) {
      w.timestamps = w.timestamps.filter((t) => now - t < w.windowMs);
      if (w.timestamps.length >= w.max) {
        const oldest = w.timestamps[0];
        waitMs = Math.max(waitMs, w.windowMs - (now - oldest) + 1000);
      }
    }
    return waitMs;
  }

  recordUse() {
    const now = Date.now();
    this.windows.forEach((w) => w.timestamps.push(now));
  }
}

// ---------------- Settings / connect screens ----------------

function populateSettingsForm() {
  const cfg = oauth.getClientConfig();
  el("clientIdInput").value = cfg ? cfg.clientId : "";
  el("redirectUriInput").value = cfg ? cfg.redirectUri : oauth.defaultRedirectUri();
}

el("saveSettingsBtn").addEventListener("click", () => {
  const clientId = el("clientIdInput").value.trim();
  const redirectUri = el("redirectUriInput").value.trim();
  if (!clientId) {
    alert("Client ID is required.");
    return;
  }
  if (!redirectUri) {
    alert("Redirect URI is required.");
    return;
  }
  oauth.setClientConfig(clientId, redirectUri);
  route();
});

el("cancelSettingsBtn").addEventListener("click", () => route());

el("settingsBtn").addEventListener("click", () => {
  populateSettingsForm();
  el("cancelSettingsBtn").classList.remove("hidden");
  showScreen("settings");
});

el("editClientBtn").addEventListener("click", () => {
  populateSettingsForm();
  el("cancelSettingsBtn").classList.remove("hidden");
  showScreen("settings");
});

el("connectBtn").addEventListener("click", async () => {
  el("connectError").classList.add("hidden");
  try {
    await oauth.startLogin();
  } catch (e) {
    el("connectError").textContent = e.message;
    el("connectError").classList.remove("hidden");
  }
});

el("disconnectBtn").addEventListener("click", async () => {
  const cfg = oauth.getClientConfig();
  const tokens = oauth.getTokens();
  if (cfg && tokens && tokens.refresh_token) {
    await api.revokeToken(tokens.refresh_token, cfg.clientId);
  }
  oauth.clearSession();
  route();
});

// ---------------- Main screen: category + date filters ----------------

let currentUser = null; // { id, username, name, public_metrics }

function initMainScreenFromPrefs() {
  const prefs = loadPrefs();
  el("catPosts").checked = prefs.categories.includes("posts");
  el("catReposts").checked = prefs.categories.includes("reposts");
  el("catLikes").checked = prefs.categories.includes("likes");

  document.querySelectorAll('input[name="mode"]').forEach((r) => {
    r.checked = r.value === (prefs.mode || "everything");
  });
  el("customStart").value = prefs.customStart || "";
  el("customEnd").value = prefs.customEnd || "";
  toggleCustomDates();
}

function getSelectedCategories() {
  const cats = [];
  if (el("catPosts").checked) cats.push("posts");
  if (el("catReposts").checked) cats.push("reposts");
  if (el("catLikes").checked) cats.push("likes");
  return cats;
}

function toggleCustomDates() {
  const mode = document.querySelector('input[name="mode"]:checked')?.value;
  el("customDates").classList.toggle("hidden", mode !== "custom");
}

["catPosts", "catReposts", "catLikes"].forEach((id) => {
  el(id).addEventListener("change", persistCurrentPrefs);
});
document.querySelectorAll('input[name="mode"]').forEach((r) => {
  r.addEventListener("change", () => {
    toggleCustomDates();
    persistCurrentPrefs();
  });
});
el("customStart").addEventListener("change", persistCurrentPrefs);
el("customEnd").addEventListener("change", persistCurrentPrefs);

function persistCurrentPrefs() {
  const mode = document.querySelector('input[name="mode"]:checked')?.value || "everything";
  savePrefs({
    categories: getSelectedCategories(),
    mode,
    customStart: el("customStart").value,
    customEnd: el("customEnd").value,
  });
}

el("mainCancelBtn").addEventListener("click", () => {
  initMainScreenFromPrefs();
  el("mainError").classList.add("hidden");
});

el("mainDeleteBtn").addEventListener("click", onDeleteClicked);

function dateOnly(d) {
  return d.toISOString().slice(0, 10);
}

function computeDateFilter() {
  const mode = document.querySelector('input[name="mode"]:checked')?.value || "everything";
  const now = new Date();

  if (mode === "everything") {
    return { mode, predicate: () => true };
  }
  if (mode === "7d" || mode === "30d") {
    const days = mode === "7d" ? 7 : 30;
    const cutoff = new Date(now.getTime() - days * 24 * 60 * 60 * 1000);
    return { mode, predicate: (createdAt) => new Date(createdAt) < cutoff };
  }
  if (mode === "custom") {
    const startStr = el("customStart").value;
    const endStr = el("customEnd").value;
    if (!startStr || !endStr) throw new Error("Pick both a start and an end date for a custom range.");
    const start = new Date(startStr + "T00:00:00");
    const end = new Date(endStr + "T23:59:59.999");
    if (start > end) throw new Error("Start date must be before end date.");
    return {
      mode,
      predicate: (createdAt) => {
        const d = new Date(createdAt);
        return d >= start && d <= end;
      },
    };
  }
  throw new Error("Pick a date range.");
}

async function onDeleteClicked() {
  el("mainError").classList.add("hidden");

  const categories = getSelectedCategories();
  if (categories.length === 0) {
    el("mainError").textContent = "Select at least one category to delete.";
    el("mainError").classList.remove("hidden");
    return;
  }

  let dateFilter;
  try {
    dateFilter = computeDateFilter();
  } catch (e) {
    el("mainError").textContent = e.message;
    el("mainError").classList.remove("hidden");
    return;
  }

  runDeletion(categories, dateFilter);
}

// ---------------- Shared confirm/cancel gate helper ----------------

function waitForGate(trueBtnId, falseBtnId) {
  return new Promise((resolve) => {
    const trueBtn = el(trueBtnId);
    const falseBtn = el(falseBtnId);
    const onTrue = () => {
      cleanup();
      resolve(true);
    };
    const onFalse = () => {
      cleanup();
      resolve(false);
    };
    function cleanup() {
      trueBtn.removeEventListener("click", onTrue);
      falseBtn.removeEventListener("click", onFalse);
    }
    trueBtn.addEventListener("click", onTrue);
    falseBtn.addEventListener("click", onFalse);
  });
}

// ---------------- Archive gate ----------------

// Shown only when the live API returned fewer items than the account's
// true lifetime totals for a category the user selected. Returns
// { cancelled, archivePosts, archiveReposts, archiveLikes }.
async function runArchiveGate({ needTweetArchive, needLikeArchive, postsGap, likesGap }) {
  showScreen("archive");
  el("archiveTweetField").classList.toggle("hidden", !needTweetArchive);
  el("archiveLikeField").classList.toggle("hidden", !needLikeArchive);
  el("archiveTweetFile").value = "";
  el("archiveLikeFile").value = "";
  el("archiveStatus").textContent = "";

  const parts = [];
  if (needTweetArchive) {
    parts.push(
      `posts/replies/reposts (the API returned roughly ${postsGap} fewer than your account's real total)`
    );
  }
  if (needLikeArchive) {
    parts.push(`likes (the API returned roughly ${likesGap} fewer than your account's real total)`);
  }
  el("archiveExplainer").textContent =
    `X's live API couldn't return your complete history for: ${parts.join(" and ")}. ` +
    `Upload the matching file(s) from your X data archive (Settings → Your Account → ` +
    `Download an archive of your data → the data folder) to fill the gap, or continue ` +
    `without it to only act on what the API found.`;

  const proceed = await waitForGate("archiveContinueBtn", "archiveCancelBtn");
  if (!proceed) return { cancelled: true };

  let archivePosts = [];
  let archiveReposts = [];
  let archiveLikes = [];

  const tweetFiles = el("archiveTweetFile").files;
  const likeFiles = el("archiveLikeFile").files;

  try {
    if (tweetFiles && tweetFiles.length > 0) {
      el("archiveStatus").textContent = "Reading archive file(s)…";
      const parsed = await archive.parseArchiveFiles(tweetFiles);
      archivePosts = parsed.posts;
      archiveReposts = parsed.reposts;
      if (parsed.warnings.length) {
        el("archiveStatus").textContent = parsed.warnings.join(" ");
      }
    }
    if (likeFiles && likeFiles.length > 0) {
      const parsed = await archive.parseArchiveFiles(likeFiles);
      archiveLikes = parsed.likes;
    }
  } catch (e) {
    el("archiveStatus").textContent = `Error reading archive file: ${e.message}`;
    el("archiveStatus").classList.add("error-text");
    await new Promise((r) => setTimeout(r, 2500));
  }

  return { cancelled: false, archivePosts, archiveReposts, archiveLikes };
}

// ---------------- Progress / deletion run ----------------

let runState = null;

function beforeUnloadHandler(e) {
  e.preventDefault();
  e.returnValue = "";
  return "";
}

function setStatus(text) {
  el("progressStatusLine").textContent = text;
}

function appendLog(line) {
  const box = el("logBox");
  box.classList.remove("hidden");
  const p = document.createElement("div");
  p.textContent = line;
  box.appendChild(p);
  box.scrollTop = box.scrollHeight;
}

function setProgressBar(done, total) {
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  el("progressBarFill").style.width = pct + "%";
  el("progressCount").textContent = `${done} / ${total}`;
}

async function sleepInterruptible(ms, tickMs = 500) {
  let remaining = ms;
  while (remaining > 0 && !runState.cancelled) {
    const step = Math.min(tickMs, remaining);
    await new Promise((r) => setTimeout(r, step));
    remaining -= step;
  }
}

async function waitWhilePaused() {
  while (runState.paused && !runState.cancelled) {
    await new Promise((r) => setTimeout(r, 300));
  }
}

function isRetweetItem(t) {
  return (t.referenced_tweets || []).some((r) => r.type === "retweeted");
}

function getRetweetSourceId(t) {
  const rt = (t.referenced_tweets || []).find((r) => r.type === "retweeted");
  return rt ? rt.id : null;
}

function dedupeById(items) {
  const map = new Map();
  for (const item of items) map.set(item.id, item);
  return Array.from(map.values());
}

// X's error bodies vary by endpoint/error type - most commonly either the
// v2-style { errors: [{ message, ... }] } or the RFC 7807-style
// { title, detail, type }. Pulls out whichever is present; falls back to
// null (caller then just shows the bare status code) rather than dumping
// raw JSON into the log.
function extractErrorReason(detailText) {
  if (!detailText) return null;
  try {
    const parsed = JSON.parse(detailText);
    if (Array.isArray(parsed.errors) && parsed.errors[0]) {
      const e = parsed.errors[0];
      return e.message || e.detail || e.title || null;
    }
    if (parsed.detail || parsed.title) {
      return [parsed.title, parsed.detail].filter(Boolean).join(": ");
    }
  } catch {
    // Not JSON (rare, but possible for some error paths) - show trimmed raw text.
    return detailText.slice(0, 200);
  }
  return null;
}

const CATEGORY_LABEL = { posts: "posts/replies", reposts: "reposts", likes: "likes" };
const CATEGORY_SINGULAR = { posts: "post/reply", reposts: "repost", likes: "like" };

async function runDeletion(categories, dateFilter) {
  showScreen("progress");
  window.addEventListener("beforeunload", beforeUnloadHandler);
  // Android shell only: ask the app to hold a foreground-service notification
  // and a wake lock for the duration of this run, since Android (unlike a
  // desktop browser tab) will suspend background JS execution otherwise.
  if (window.AndroidBridge) window.AndroidBridge.notifyDeletionActive(true);

  runState = { cancelled: false, paused: false };
  el("logBox").classList.add("hidden");
  el("logBox").innerHTML = "";
  el("pauseResumeBtn").classList.add("hidden");
  el("startDeleteBtn").classList.add("hidden");
  el("progressBarWrap").classList.add("hidden");
  el("progressFailCount").textContent = "";
  setProgressBar(0, 0);
  el("progressSummaryLine").textContent = "Fetching your account details…";
  setStatus("");

  try {
    currentUser = await api.getMe();
    const metrics = currentUser.public_metrics || {};

    const wantsPosts = categories.includes("posts") || categories.includes("reposts");
    const wantsLikes = categories.includes("likes");

    let rawPosts = [];
    let postsHitLimit = false;
    let postsGap = 0;

    if (wantsPosts) {
      el("progressSummaryLine").textContent = "Fetching your posts and reposts…";
      rawPosts = await api.fetchAllPosts(currentUser.id, {
        shouldStop: () => runState.cancelled,
        onPage: (page, totalSoFar, meta) => {
          if (meta && meta.rateLimitedSeconds) {
            setStatus(`Rate limited while fetching posts — resuming in ~${meta.rateLimitedSeconds}s…`);
          } else {
            setStatus(`Fetched ${totalSoFar} posts so far…`);
          }
        },
      });
      if (runState.cancelled) {
        finishRun({ cancelled: true, results: [] });
        return;
      }
      if (typeof metrics.tweet_count === "number" && rawPosts.length < metrics.tweet_count) {
        postsHitLimit = true;
        postsGap = metrics.tweet_count - rawPosts.length;
      }
    }

    let rawLikes = [];
    let likesHitLimit = false;
    let likesGap = 0;

    if (wantsLikes) {
      el("progressSummaryLine").textContent = "Fetching your likes…";
      rawLikes = await api.fetchAllLikedPosts(currentUser.id, {
        shouldStop: () => runState.cancelled,
        onPage: (page, totalSoFar, meta) => {
          if (meta && meta.rateLimitedSeconds) {
            setStatus(`Rate limited while fetching likes — resuming in ~${meta.rateLimitedSeconds}s…`);
          } else {
            setStatus(`Fetched ${totalSoFar} likes so far…`);
          }
        },
      });
      if (runState.cancelled) {
        finishRun({ cancelled: true, results: [] });
        return;
      }
      if (typeof metrics.like_count === "number" && rawLikes.length < metrics.like_count) {
        likesHitLimit = true;
        likesGap = metrics.like_count - rawLikes.length;
      }
    }

    // ---- Offer the archive only if something was actually missed ----
    let archivePosts = [];
    let archiveReposts = [];
    let archiveLikes = [];

    if (postsHitLimit || likesHitLimit) {
      const gateResult = await runArchiveGate({
        needTweetArchive: postsHitLimit,
        needLikeArchive: likesHitLimit,
        postsGap,
        likesGap,
      });
      if (gateResult.cancelled) {
        finishRun({ cancelled: true, results: [] });
        return;
      }
      archivePosts = gateResult.archivePosts || [];
      archiveReposts = gateResult.archiveReposts || [];
      archiveLikes = gateResult.archiveLikes || [];
      showScreen("progress");
    }

    // ---- Build the target list ----
    const postsReplies = dedupeById(rawPosts.filter((t) => !isRetweetItem(t)).concat(archivePosts));
    const repostsRaw = rawPosts
      .filter(isRetweetItem)
      .map((t) => ({ id: getRetweetSourceId(t), created_at: t.created_at, referenced_tweets: t.referenced_tweets }));
    const reposts = dedupeById(repostsRaw.concat(archiveReposts));
    const likes = dedupeById(rawLikes.concat(archiveLikes));

    let targets = [];
    if (categories.includes("posts")) {
      targets.push(...postsReplies.filter((t) => dateFilter.predicate(t.created_at)).map((t) => ({ ...t, category: "posts" })));
    }
    if (categories.includes("reposts")) {
      targets.push(...reposts.filter((t) => dateFilter.predicate(t.created_at)).map((t) => ({ ...t, category: "reposts" })));
    }
    if (categories.includes("likes")) {
      targets.push(...likes.filter((t) => dateFilter.predicate(t.created_at)).map((t) => ({ ...t, category: "likes" })));
    }

    if (targets.length === 0) {
      finishRun({ cancelled: false, results: [], empty: true });
      return;
    }

    const dates = targets.map((t) => new Date(t.created_at).getTime());
    const minDate = dateOnly(new Date(Math.min(...dates)));
    const maxDate = dateOnly(new Date(Math.max(...dates)));

    const counts = { posts: 0, reposts: 0, likes: 0 };
    targets.forEach((t) => counts[t.category]++);
    const breakdown = Object.entries(counts)
      .filter(([, n]) => n > 0)
      .map(([cat, n]) => `${n} ${CATEGORY_LABEL[cat]}`)
      .join(", ");

    el("progressSummaryLine").textContent =
      `${targets.length} items to be deleted (${breakdown}) between ${minDate} and ${maxDate}`;
    setStatus('Review the count above, then click "Start deleting" to proceed, or Cancel to back out.');

    el("startDeleteBtn").classList.remove("hidden");
    const proceedToDelete = await waitForGate("startDeleteBtn", "progressCancelBtn");
    el("startDeleteBtn").classList.add("hidden");

    if (!proceedToDelete || runState.cancelled) {
      finishRun({ cancelled: true, results: [] });
      return;
    }
    el("progressBarWrap").classList.remove("hidden");
    el("pauseResumeBtn").classList.remove("hidden");
    el("pauseResumeBtn").textContent = "Pause";
    setProgressBar(0, targets.length);
    setStatus("");

    // ---- Delete, paced independently per category (each has its own
    // rate-limit bucket on X's side) ----
    const limiters = {
      posts: new RateLimiter([{ max: 50, windowMs: 15 * 60 * 1000 }]),
      reposts: new RateLimiter([{ max: 50, windowMs: 15 * 60 * 1000 }]),
      likes: new RateLimiter([
        { max: 50, windowMs: 15 * 60 * 1000 },
        { max: 1000, windowMs: 24 * 60 * 60 * 1000 },
      ]),
    };

    let deleted = 0;
    let failed = 0;
    const resultLog = [];

    for (const item of targets) {
      if (runState.cancelled) break;
      await waitWhilePaused();
      if (runState.cancelled) break;

      const limiter = limiters[item.category];
      const waitMs = limiter.msUntilFree();
      if (waitMs > 0) {
        setStatus(`Rate limit reached for ${CATEGORY_LABEL[item.category]} — resuming in ~${Math.ceil(waitMs / 1000)}s…`);
        await sleepInterruptible(waitMs);
        if (runState.cancelled) break;
        await waitWhilePaused();
        if (runState.cancelled) break;
      }

      setStatus(`Removing ${CATEGORY_SINGULAR[item.category]} ${item.id}…`);

      let result;
      if (item.category === "posts") {
        result = await api.deletePost(item.id);
      } else if (item.category === "reposts") {
        result = await api.undoRepost(currentUser.id, item.id);
      } else {
        result = await api.undoLike(currentUser.id, item.id);
      }
      limiter.recordUse();

      if (result.ok) {
        deleted++;
        resultLog.push({ id: item.id, category: item.category, created_at: item.created_at, status: "deleted" });
      } else {
        failed++;
        // result.detail is X's own error body (when it sent one) - a bare
        // "HTTP 403" tells you nothing about *why*. Pull out a short,
        // human-readable reason from it when possible, and keep the raw
        // text too so the CSV export has the full picture.
        const reason = extractErrorReason(result.detail);
        const detailText = reason ? `HTTP ${result.status} — ${reason}` : `HTTP ${result.status}`;
        resultLog.push({
          id: item.id,
          category: item.category,
          created_at: item.created_at,
          status: "failed",
          detail: detailText,
        });
        appendLog(`Failed to remove ${CATEGORY_SINGULAR[item.category]} ${item.id}: ${detailText}`);
      }

      setProgressBar(deleted + failed, targets.length);
      el("progressFailCount").textContent = failed > 0 ? `${failed} failed` : "";
    }

    finishRun({ cancelled: runState.cancelled, results: resultLog, total: targets.length });
  } catch (e) {
    setStatus("");
    appendLog(`Error: ${e.message}`);
    finishRun({ cancelled: true, error: e.message, results: [] });
  }
}

el("pauseResumeBtn").addEventListener("click", () => {
  if (!runState) return;
  runState.paused = !runState.paused;
  el("pauseResumeBtn").textContent = runState.paused ? "Continue" : "Pause";
  setStatus(runState.paused ? "Paused. Click Continue to resume." : "");
});

el("progressCancelBtn").addEventListener("click", () => {
  if (!runState) return;
  const alreadyDeleting = !el("pauseResumeBtn").classList.contains("hidden");
  const message = alreadyDeleting
    ? "Cancel the run? Items already removed stay removed."
    : "Cancel and go back without deleting anything?";
  if (window.confirm(message)) {
    runState.cancelled = true;
    runState.paused = false;
  }
});

let lastLog = [];

function finishRun({ cancelled, results = [], total = 0, empty = false, error = null }) {
  window.removeEventListener("beforeunload", beforeUnloadHandler);
  if (window.AndroidBridge) window.AndroidBridge.notifyDeletionActive(false);
  lastLog = results;

  const deleted = results.filter((r) => r.status === "deleted").length;
  const failed = results.filter((r) => r.status === "failed").length;
  const effectiveTotal = total || results.length;

  const icon = el("doneIcon");
  const title = el("doneTitle");
  const message = el("doneMessage");

  if (error) {
    icon.textContent = "!";
    icon.style.background = "var(--error)";
    title.textContent = "Run failed";
    message.textContent = error;
  } else if (empty) {
    icon.textContent = "i";
    icon.style.background = "var(--text-muted)";
    title.textContent = "Nothing to delete";
    message.textContent = "Nothing matched your selected categories and date range.";
  } else if (cancelled && results.length === 0) {
    icon.textContent = "!";
    icon.style.background = "var(--warning)";
    title.textContent = "Cancelled";
    message.textContent = "Nothing was deleted.";
  } else if (cancelled) {
    icon.textContent = "!";
    icon.style.background = "var(--warning)";
    title.textContent = "Cancelled";
    message.textContent = `Stopped early. Removed ${deleted} of ${effectiveTotal}` + (failed ? ` (${failed} failed).` : ".");
  } else {
    icon.textContent = "✓";
    icon.style.background = "var(--success)";
    title.textContent = "Run complete";
    message.textContent = `Removed ${deleted} of ${effectiveTotal} successfully` + (failed ? `, ${failed} failed — see the log.` : ".");
  }

  el("downloadLogBtn").classList.toggle("hidden", lastLog.length === 0);
  showScreen("done");
}

el("downloadLogBtn").addEventListener("click", () => {
  const rows = [["id", "category", "created_at", "status", "detail"]];
  lastLog.forEach((r) => rows.push([r.id, r.category, r.created_at, r.status, r.detail || ""]));
  const csv = rows.map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
  const filename = `tweetdelete-log-${Date.now()}.csv`;
  // Android WebView can't follow a blob: URL through an <a download> click
  // (blob: URLs only resolve inside this page's own JS context), so the
  // Android shell exposes a bridge that writes straight to the device's
  // Downloads folder instead. Desktop/browser use keeps the normal path.
  if (window.AndroidBridge && window.AndroidBridge.saveCsvLog) {
    window.AndroidBridge.saveCsvLog(filename, csv);
    return;
  }
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
});

el("doneBackBtn").addEventListener("click", () => route());

// ---------------- Routing ----------------

async function route() {
  const cfg = oauth.getClientConfig();
  if (!cfg) {
    populateSettingsForm();
    el("cancelSettingsBtn").classList.add("hidden");
    showScreen("settings");
    updateAccountBadge(null);
    return;
  }

  if (!oauth.isLoggedIn()) {
    showScreen("connect");
    updateAccountBadge(null);
    return;
  }

  try {
    if (!currentUser) currentUser = await api.getMe();
    updateAccountBadge(currentUser);
    initMainScreenFromPrefs();
    showScreen("main");
  } catch (e) {
    oauth.clearSession();
    el("connectError").textContent = e.message;
    el("connectError").classList.remove("hidden");
    showScreen("connect");
    updateAccountBadge(null);
  }
}

function updateAccountBadge(user) {
  const badge = el("accountBadge");
  if (user) {
    el("accountHandle").textContent = `@${user.username}`;
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
}

route();
