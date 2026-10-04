// runner.js — one async interface to the background deletion runner, which
// lives outside the page so rate-limit waits survive tab throttling, sleeping
// tabs, minimised windows and closing the window.
//
//   Desktop (Windows tray app / Linux service): runner.py inside the local
//     helper, reached over same-origin HTTP at /__runner/...
//   Android: DeletionEngine.kt via the synchronous window.AndroidBridge.
//
// If neither is present (e.g. an old helper), available() is false and
// app.js falls back to its in-page loop.

const HEADERS = { "X-TweetDelete": "1" };

let desktopAvailable = null; // cached probe result

function android() {
  try {
    return window.AndroidBridge && window.AndroidBridge.hasNativeRunner && window.AndroidBridge.hasNativeRunner()
      ? window.AndroidBridge
      : null;
  } catch {
    return null;
  }
}

async function call(method, action, body) {
  const opts = { method, headers: { ...HEADERS }, cache: "no-store" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`/__runner/${action}`, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok && res.status !== 409) throw new Error(`Runner request failed (${res.status})`);
  return data;
}

export async function available() {
  if (android()) return true;
  if (desktopAvailable !== null) return desktopAvailable;
  try {
    const res = await fetch("/__runner/status", { headers: HEADERS, cache: "no-store" });
    desktopAvailable = res.ok;
  } catch {
    desktopAvailable = false;
  }
  return desktopAvailable;
}

export function isAndroid() {
  return !!android();
}

// Returns an error message, or null on success.
export async function start(payload) {
  const a = android();
  if (a) return a.startNativeRun(JSON.stringify(payload)) || null;
  const r = await call("POST", "start", payload);
  return r.error || null;
}

export async function status() {
  const a = android();
  if (a) return JSON.parse(a.getRunStatus());
  return call("GET", "status");
}

export async function results(from = 0) {
  const a = android();
  if (a) return JSON.parse(a.getRunResults(from));
  return call("GET", `results?from=${encodeURIComponent(from)}`);
}

export async function setPaused(paused) {
  const a = android();
  if (a) return a.pauseNativeRun(paused);
  return call("POST", "pause", { paused });
}

export async function cancel() {
  const a = android();
  if (a) return a.cancelNativeRun();
  return call("POST", "cancel", {});
}

export async function clear() {
  const a = android();
  if (a) return a.clearNativeRun();
  return call("POST", "clear", {});
}

// Tokens the runner refreshed (X rotates refresh tokens), or null.
export async function takeUpdatedTokens() {
  const a = android();
  if (a) {
    const t = a.takeUpdatedTokens();
    return t ? JSON.parse(t) : null;
  }
  const r = await call("POST", "tokens", {});
  return r.tokens || null;
}
