// oauth.js — OAuth 2.0 Authorization Code flow with PKCE, for a public
// (browser-only) client. No client secret is ever used or stored.
//
// X's OAuth docs: https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code

const AUTHORIZE_URL = "https://x.com/i/oauth2/authorize";
// Token endpoint is called through the local proxy (server.py) because
// api.x.com does not send CORS headers, so a direct browser fetch would be
// blocked. The proxy just forwards this request verbatim server-side.
const TOKEN_URL = "/api/x/2/oauth2/token";

const SCOPES = "tweet.read tweet.write like.read like.write users.read offline.access";

const LS_CLIENT = "td_client_config";
const LS_TOKENS = "td_tokens";
const SS_VERIFIER = "td_code_verifier";
const SS_STATE = "td_oauth_state";

function base64url(bytes) {
  let str = btoa(String.fromCharCode(...bytes));
  return str.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomString(len) {
  const bytes = new Uint8Array(len);
  crypto.getRandomValues(bytes);
  return base64url(bytes);
}

async function sha256Challenge(verifier) {
  const data = new TextEncoder().encode(verifier);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return base64url(new Uint8Array(digest));
}

// ---- Client configuration (Client ID + redirect URI). Not secret. ----

export function getClientConfig() {
  try {
    return JSON.parse(localStorage.getItem(LS_CLIENT) || "null");
  } catch {
    return null;
  }
}

export function setClientConfig(clientId, redirectUri) {
  localStorage.setItem(
    LS_CLIENT,
    JSON.stringify({ clientId: clientId.trim(), redirectUri: redirectUri.trim() })
  );
}

export function defaultRedirectUri() {
  return `${window.location.origin}/callback.html`;
}

// ---- Token storage ----

export function getTokens() {
  try {
    return JSON.parse(localStorage.getItem(LS_TOKENS) || "null");
  } catch {
    return null;
  }
}

function setTokens(tokens) {
  localStorage.setItem(LS_TOKENS, JSON.stringify(tokens));
}

export function clearSession() {
  localStorage.removeItem(LS_TOKENS);
  sessionStorage.removeItem(SS_VERIFIER);
  sessionStorage.removeItem(SS_STATE);
}

export function isLoggedIn() {
  const t = getTokens();
  return !!(t && t.access_token);
}

// ---- Authorization Code + PKCE flow ----

export async function startLogin() {
  const cfg = getClientConfig();
  if (!cfg || !cfg.clientId) throw new Error("Client ID not configured yet.");

  const verifier = randomString(64);
  const challenge = await sha256Challenge(verifier);
  const state = randomString(24);

  sessionStorage.setItem(SS_VERIFIER, verifier);
  sessionStorage.setItem(SS_STATE, state);

  const params = new URLSearchParams({
    response_type: "code",
    client_id: cfg.clientId,
    redirect_uri: cfg.redirectUri,
    scope: SCOPES,
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });

  window.location.href = `${AUTHORIZE_URL}?${params.toString()}`;
}

export async function completeLogin(code, state) {
  const cfg = getClientConfig();
  const expectedState = sessionStorage.getItem(SS_STATE);
  const verifier = sessionStorage.getItem(SS_VERIFIER);

  if (!cfg || !cfg.clientId) throw new Error("Client ID not configured.");
  if (!verifier || !expectedState) throw new Error("No pending login found in this browser tab.");
  if (state !== expectedState) throw new Error("State mismatch — possible tampering. Login aborted.");

  const body = new URLSearchParams({
    grant_type: "authorization_code",
    code,
    redirect_uri: cfg.redirectUri,
    client_id: cfg.clientId,
    code_verifier: verifier,
  });

  const res = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });

  sessionStorage.removeItem(SS_VERIFIER);
  sessionStorage.removeItem(SS_STATE);

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Token exchange failed (${res.status}): ${text}`);
  }

  const data = await res.json();
  setTokens({
    access_token: data.access_token,
    refresh_token: data.refresh_token || null,
    expires_at: Date.now() + (data.expires_in || 7200) * 1000,
  });
}

async function refreshAccessToken() {
  const t = getTokens();
  const cfg = getClientConfig();
  if (!t || !t.refresh_token || !cfg) return false;

  const body = new URLSearchParams({
    grant_type: "refresh_token",
    refresh_token: t.refresh_token,
    client_id: cfg.clientId,
  });

  const res = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });

  if (!res.ok) return false;

  const data = await res.json();
  setTokens({
    access_token: data.access_token,
    refresh_token: data.refresh_token || t.refresh_token,
    expires_at: Date.now() + (data.expires_in || 7200) * 1000,
  });
  return true;
}

// Returns a valid access token, refreshing first if it has expired (or is
// about to, within 60s). Throws if re-login is required.
export async function getValidAccessToken() {
  let t = getTokens();
  if (!t || !t.access_token) throw new Error("Not logged in.");

  if (Date.now() > t.expires_at - 60000) {
    const ok = t.refresh_token ? await refreshAccessToken() : false;
    if (!ok) {
      clearSession();
      throw new Error("Session expired. Please connect to X again.");
    }
    t = getTokens();
  }
  return t.access_token;
}
