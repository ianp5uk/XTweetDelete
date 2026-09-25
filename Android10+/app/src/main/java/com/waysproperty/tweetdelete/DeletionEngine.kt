package com.waysproperty.tweetdelete

import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import okhttp3.FormBody
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Native deletion runner (added in v1.0.3).
 *
 * Why this exists: up to v1.0.2 the deletion loop ran as JavaScript inside
 * the WebView. Once the app is in the background (or the screen is off) the
 * WebView page is "hidden", and Chromium throttles or suspends a hidden
 * page's timers regardless of the foreground service, wake lock or battery
 * settings - those keep the *app process* alive, not the WebView renderer's
 * JS timers. The 15-minute rate-limit wait was a JS timer loop that counted
 * 500 ms ticks rather than checking the wall clock, so once throttled it
 * effectively never finished. That is why runs stalled after the first 50.
 *
 * This class moves the actual delete loop (and its rate-limit waits, token
 * refresh and retry logic) into plain Kotlin running inside the foreground
 * service. The WebView UI still does login, fetching, filtering and the
 * archive step, then hands the final target list here and just displays
 * progress. Waits are measured against the wall clock, so even if the
 * process is briefly starved they end at the right time.
 *
 * The run state is saved to app-private storage after every item, so if
 * Android kills the process anyway, the run resumes from where it left off
 * as soon as the service is recreated (sticky restart or reopening the app).
 */
class DeletionEngine(
    private val filesDir: File,
    private val onChange: () -> Unit,
    private val keepAwake: (Boolean) -> Unit,
    private val apiBase: String = API,
) {
    companion object {
        private const val TAG = "TweetDelete"
        private const val STATE_FILE = "run_state.json"
        private const val API = "https://api.x.com"
        private const val MIN_15 = 15 * 60 * 1000L
        private const val HOURS_24 = 24 * 60 * 60 * 1000L
        private const val MAX_CONSECUTIVE_FAILURES = 10
        private const val MAX_NETWORK_BACKOFF = 5 * 60 * 1000L

        private val LIMITS = mapOf(
            "posts" to listOf(50 to MIN_15),
            "reposts" to listOf(50 to MIN_15),
            "likes" to listOf(50 to MIN_15, 1000 to HOURS_24),
        )
        private val SINGULAR = mapOf("posts" to "post/reply", "reposts" to "repost", "likes" to "like")
        private val PLURAL = mapOf("posts" to "posts/replies", "reposts" to "reposts", "likes" to "likes")
    }

    private data class Item(val id: String, val category: String, val createdAt: String)

    private sealed class Outcome {
        data class Done(val ok: Boolean, val detail: String?) : Outcome()
        data class RateLimited(val until: Long) : Outcome()
        data class Network(val message: String) : Outcome()
        object AuthFailed : Outcome()
    }

    private val http = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var job: Job? = null
    private val lock = Any()
    private val stateFile get() = File(filesDir, STATE_FILE)

    // ---- Persisted run state (all guarded by `lock`) ----
    private var state = "idle" // idle | running | finished
    private var items = ArrayList<Item>()
    private var nextIndex = 0
    private var results = JSONArray()
    private var deleted = 0
    private var failed = 0
    private var userId = ""
    private var username = ""
    private var clientId = ""
    private var accessToken = ""
    private var refreshToken: String? = null
    private var expiresAt = 0L
    private var tokensUpdated = false
    private val windowUse = HashMap<String, ArrayList<Long>>()
    private val blockedUntil = HashMap<String, Long>()
    private var paused = false
    private var pauseReason: String? = null
    private var cancelled = false
    private var errorMsg: String? = null

    // ---- Transient, UI-only ----
    @Volatile private var statusText = ""
    @Volatile private var waitUntil = 0L
    // "limit" (normal rate-limit wait), "xdelay" (X still refused after a
    // wait ended) or "network". Drives the countdown wording in the UI.
    @Volatile private var waitKind = ""
    // True from the end of a rate-limit wait until X accepts a request again.
    @Volatile private var awaitingX = false

    val isRunning: Boolean get() = synchronized(lock) { state == "running" }

    /** Called from the service's onCreate: resumes an interrupted run, if any. */
    fun restore() {
        synchronized(lock) {
            val f = stateFile
            if (!f.exists()) return
            try {
                fromJson(JSONObject(f.readText()))
            } catch (e: Exception) {
                Log.e(TAG, "Could not read saved run state; discarding it", e)
                f.delete()
                return
            }
        }
        if (isRunning) {
            statusText = "Resuming interrupted run…"
            launchLoop()
        }
    }

    /** Starts a new run. Returns an error message, or null on success. */
    fun start(payloadJson: String): String? {
        synchronized(lock) {
            if (state == "running") return "A run is already in progress."
            val p = JSONObject(payloadJson)
            val arr = p.getJSONArray("targets")
            val list = ArrayList<Item>(arr.length())
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                list.add(Item(o.getString("id"), o.getString("category"), o.optString("created_at", "")))
            }
            val t = p.getJSONObject("tokens")
            items = list
            nextIndex = 0
            results = JSONArray()
            deleted = 0
            failed = 0
            userId = p.getString("userId")
            username = p.optString("username", "")
            clientId = p.getString("clientId")
            accessToken = t.getString("access_token")
            refreshToken = optStr(t, "refresh_token")
            expiresAt = t.optLong("expires_at", 0L)
            tokensUpdated = false
            windowUse.clear()
            blockedUntil.clear()
            paused = false
            pauseReason = null
            cancelled = false
            errorMsg = null
            state = "running"
            persistLocked()
        }
        statusText = "Starting…"
        launchLoop()
        return null
    }

    fun setPaused(p: Boolean) {
        synchronized(lock) {
            if (state != "running") return
            paused = p
            if (!p) pauseReason = null
            persistLocked()
        }
        onChange()
    }

    fun cancel() {
        synchronized(lock) {
            if (state != "running") return
            cancelled = true
            paused = false
            persistLocked()
        }
        onChange()
    }

    /** Clears a finished run once the UI has shown/exported its results. */
    fun clear() {
        synchronized(lock) {
            if (state == "running") return
            state = "idle"
            items = ArrayList()
            results = JSONArray()
            stateFile.delete()
        }
    }

    fun statusJson(): String = synchronized(lock) {
        JSONObject().apply {
            put("state", state)
            put("paused", paused)
            put("pauseReason", pauseReason ?: JSONObject.NULL)
            put("cancelled", cancelled)
            put("total", items.size)
            put("deleted", deleted)
            put("failed", failed)
            put("status", statusText)
            put("waitUntil", waitUntil)
            put("waitKind", waitKind)
            put("awaitingX", awaitingX)
            put("now", System.currentTimeMillis())
            put("error", errorMsg ?: JSONObject.NULL)
            put("username", username)
            put("userId", userId)
            put("resultCount", results.length())
        }.toString()
    }

    /** Results (deleted + failed rows) from index `from` onward. */
    fun resultsJson(from: Int): String = synchronized(lock) {
        val out = JSONArray()
        for (i in from.coerceAtLeast(0) until results.length()) out.put(results.get(i))
        out.toString()
    }

    /** Returns the latest tokens once after the engine has refreshed them, else "". */
    fun takeUpdatedTokens(): String = synchronized(lock) {
        if (!tokensUpdated) return ""
        tokensUpdated = false
        persistLocked()
        JSONObject().apply {
            put("access_token", accessToken)
            put("refresh_token", refreshToken ?: JSONObject.NULL)
            put("expires_at", expiresAt)
        }.toString()
    }

    // ------------------------------------------------------------------

    private fun launchLoop() {
        if (job?.isActive == true) return
        keepAwake(true)
        onChange()
        job = scope.launch {
            try {
                runLoop()
            } catch (e: Exception) {
                Log.e(TAG, "Deletion loop crashed", e)
                synchronized(lock) {
                    errorMsg = "Unexpected error: ${e.message}"
                    finishLocked()
                }
            } finally {
                waitUntil = 0L
                keepAwake(false)
                onChange()
            }
        }
    }

    private suspend fun runLoop() {
        var consecutiveFailures = 0
        var lastFailStatus = ""
        var networkBackoff = 15_000L

        while (true) {
            keepAwake(true) // renews the wake-lock timeout each iteration

            val item: Item
            synchronized(lock) {
                if (cancelled || nextIndex >= items.size) {
                    finishLocked()
                    return
                }
                item = items[nextIndex]
            }

            if (isPaused()) {
                statusText = pauseReasonOr("Paused. Tap Continue to resume.")
                waitUntil = 0L
                onChange()
                while (isPaused() && !isCancelled()) delay(1000)
                continue
            }

            val wait = msUntilFree(item.category)
            if (wait > 0) {
                val until = System.currentTimeMillis() + wait
                waitKind = if (awaitingX) "xdelay" else "limit"
                waitUntil = until
                statusText = if (awaitingX) "Waiting for X to accept resumption"
                    else "Rate limit reached for ${PLURAL[item.category]}"
                onChange()
                sleepUntil(until)
                waitUntil = 0L
                awaitingX = true
                statusText = "Waiting for X to accept resumption…"
                onChange()
                continue
            }

            if (!awaitingX) {
                statusText = "Removing ${SINGULAR[item.category]} ${item.id}…"
                onChange()
            }

            when (val outcome = attemptDelete(item)) {
                is Outcome.RateLimited -> {
                    synchronized(lock) {
                        blockedUntil[item.category] = outcome.until
                        persistLocked()
                    }
                    // Loop round; msUntilFree() now reports the wait.
                }
                is Outcome.Network -> {
                    val until = System.currentTimeMillis() + networkBackoff
                    waitKind = "network"
                    waitUntil = until
                    statusText = "Network problem (${outcome.message}) — retrying"
                    onChange()
                    sleepUntil(until)
                    waitUntil = 0L
                    networkBackoff = (networkBackoff * 2).coerceAtMost(MAX_NETWORK_BACKOFF)
                }
                is Outcome.AuthFailed -> {
                    synchronized(lock) {
                        errorMsg = "Your X session expired and could not be refreshed. " +
                            "Connect to X again and start a new run; items already removed stay removed."
                        finishLocked()
                    }
                    return
                }
                is Outcome.Done -> {
                    networkBackoff = 15_000L
                    awaitingX = false
                    synchronized(lock) {
                        val row = JSONObject()
                            .put("id", item.id)
                            .put("category", item.category)
                            .put("created_at", item.createdAt)
                            .put("status", if (outcome.ok) "deleted" else "failed")
                        if (!outcome.ok) row.put("detail", outcome.detail ?: "")
                        results.put(row)
                        if (outcome.ok) deleted++ else failed++
                        nextIndex++
                        persistLocked()
                    }
                    if (outcome.ok) {
                        consecutiveFailures = 0
                    } else {
                        val code = outcome.detail?.split(" ")?.getOrNull(1) ?: ""
                        consecutiveFailures = if (code == lastFailStatus) consecutiveFailures + 1 else 1
                        lastFailStatus = code
                        if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
                            // Something systemic (e.g. API credits exhausted, app
                            // permissions changed): stop hammering X and let the
                            // user look before carrying on.
                            synchronized(lock) {
                                paused = true
                                pauseReason = "Paused automatically after $consecutiveFailures failures in a row " +
                                    "(${outcome.detail}). Check the log, then tap Continue or Cancel."
                                persistLocked()
                            }
                            consecutiveFailures = 0
                        }
                    }
                    onChange()
                }
            }
        }
    }

    private fun isPaused() = synchronized(lock) { paused }
    private fun isCancelled() = synchronized(lock) { cancelled }
    private fun pauseReasonOr(default: String) = synchronized(lock) { pauseReason ?: default }

    /** Wall-clock based sleep; wakes early on cancel. */
    private suspend fun sleepUntil(deadline: Long) {
        while (!isCancelled()) {
            val remaining = deadline - System.currentTimeMillis()
            if (remaining <= 0) return
            keepAwake(true)
            delay(remaining.coerceAtMost(5_000L))
        }
    }

    private fun msUntilFree(category: String): Long = synchronized(lock) {
        val now = System.currentTimeMillis()
        var wait = (blockedUntil[category] ?: 0L) - now
        val uses = windowUse.getOrPut(category) { ArrayList() }
        uses.removeAll { now - it >= HOURS_24 }
        for ((max, windowMs) in LIMITS[category] ?: emptyList()) {
            val inWindow = uses.filter { now - it < windowMs }
            if (inWindow.size >= max) {
                val oldestRelevant = inWindow[inWindow.size - max]
                wait = maxOf(wait, windowMs - (now - oldestRelevant) + 1000)
            }
        }
        wait
    }

    private fun recordUse(category: String) = synchronized(lock) {
        windowUse.getOrPut(category) { ArrayList() }.add(System.currentTimeMillis())
    }

    private fun attemptDelete(item: Item, allowAuthRetry: Boolean = true): Outcome {
        val token = try {
            ensureToken()
        } catch (e: IOException) {
            return Outcome.Network(e.message ?: "connection failed")
        } ?: return Outcome.AuthFailed

        val uid = synchronized(lock) { userId }
        val url = when (item.category) {
            "posts" -> "$apiBase/2/tweets/${item.id}"
            "reposts" -> "$apiBase/2/users/$uid/retweets/${item.id}"
            else -> "$apiBase/2/users/$uid/likes/${item.id}"
        }
        val req = Request.Builder().url(url).delete().header("Authorization", "Bearer $token").build()

        return try {
            http.newCall(req).execute().use { resp ->
                recordUse(item.category)
                val now = System.currentTimeMillis()
                val resetMs = resp.header("x-rate-limit-reset")?.toLongOrNull()?.times(1000)
                val remaining = resp.header("x-rate-limit-remaining")?.toIntOrNull()

                if (resp.code == 429) {
                    val until = if (resetMs != null && resetMs > now) resetMs + 2000 else now + 60_000
                    return Outcome.RateLimited(until)
                }
                if (remaining == 0 && resetMs != null && resetMs > now) {
                    synchronized(lock) { blockedUntil[item.category] = resetMs + 2000 }
                }
                if (resp.code == 401 && allowAuthRetry) {
                    val refreshed = try { refreshTokens() } catch (e: IOException) {
                        return Outcome.Network(e.message ?: "connection failed")
                    }
                    return if (refreshed) attemptDelete(item, allowAuthRetry = false) else Outcome.AuthFailed
                }
                val body = resp.body?.string() ?: ""
                if (resp.isSuccessful) {
                    Outcome.Done(true, null)
                } else {
                    val reason = extractErrorReason(body)
                    Outcome.Done(false, if (reason != null) "HTTP ${resp.code} — $reason" else "HTTP ${resp.code}")
                }
            }
        } catch (e: IOException) {
            Outcome.Network(e.message ?: "connection failed")
        }
    }

    /** Valid access token, refreshing if within 60 s of expiry; null if re-login needed. */
    private fun ensureToken(): String? {
        val (tok, exp) = synchronized(lock) { accessToken to expiresAt }
        if (System.currentTimeMillis() < exp - 60_000) return tok
        return if (refreshTokens()) synchronized(lock) { accessToken } else null
    }

    /** X rotates refresh tokens, so the new pair is saved immediately and handed back to the UI. */
    private fun refreshTokens(): Boolean {
        val (rt, cid) = synchronized(lock) { refreshToken to clientId }
        if (rt.isNullOrEmpty()) return false
        val form = FormBody.Builder()
            .add("grant_type", "refresh_token")
            .add("refresh_token", rt)
            .add("client_id", cid)
            .build()
        val req = Request.Builder().url("$apiBase/2/oauth2/token").post(form).build()
        http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) {
                Log.w(TAG, "Token refresh failed: HTTP ${resp.code}")
                return false
            }
            val data = JSONObject(resp.body?.string() ?: "{}")
            val newAccess = data.optString("access_token", "")
            if (newAccess.isEmpty()) return false
            synchronized(lock) {
                accessToken = newAccess
                val newRefresh = data.optString("refresh_token", "")
                if (newRefresh.isNotEmpty()) refreshToken = newRefresh
                expiresAt = System.currentTimeMillis() + data.optLong("expires_in", 7200) * 1000
                tokensUpdated = true
                persistLocked()
            }
            return true
        }
    }

    private fun extractErrorReason(text: String): String? {
        if (text.isBlank()) return null
        return try {
            val o = JSONObject(text)
            val errs = o.optJSONArray("errors")
            if (errs != null && errs.length() > 0) {
                val e = errs.getJSONObject(0)
                listOf("message", "detail", "title").map { e.optString(it, "") }.firstOrNull { it.isNotEmpty() }
            } else {
                listOf(o.optString("title", ""), o.optString("detail", ""))
                    .filter { it.isNotEmpty() }.joinToString(": ").ifEmpty { null }
            }
        } catch (_: Exception) {
            text.take(200)
        }
    }

    private fun finishLocked() {
        state = "finished"
        paused = false
        statusText = ""
        persistLocked()
    }

    // ---- Persistence ----

    private fun persistLocked() {
        try {
            val tmp = File(filesDir, "$STATE_FILE.tmp")
            tmp.writeText(toJson().toString())
            if (!tmp.renameTo(stateFile)) {
                stateFile.delete()
                tmp.renameTo(stateFile)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Could not save run state", e)
        }
    }

    private fun toJson(): JSONObject = JSONObject().apply {
        put("state", state)
        put("items", JSONArray().also { a ->
            items.forEach { a.put(JSONObject().put("id", it.id).put("category", it.category).put("created_at", it.createdAt)) }
        })
        put("nextIndex", nextIndex)
        put("results", results)
        put("deleted", deleted)
        put("failed", failed)
        put("userId", userId)
        put("username", username)
        put("clientId", clientId)
        put("accessToken", accessToken)
        put("refreshToken", refreshToken ?: JSONObject.NULL)
        put("expiresAt", expiresAt)
        put("tokensUpdated", tokensUpdated)
        put("windowUse", JSONObject().also { o ->
            windowUse.forEach { (k, v) -> o.put(k, JSONArray(v)) }
        })
        put("blockedUntil", JSONObject().also { o -> blockedUntil.forEach { (k, v) -> o.put(k, v) } })
        put("paused", paused)
        put("pauseReason", pauseReason ?: JSONObject.NULL)
        put("cancelled", cancelled)
        put("error", errorMsg ?: JSONObject.NULL)
    }

    private fun optStr(o: JSONObject, key: String): String? =
        if (!o.has(key) || o.isNull(key)) null else o.getString(key).ifEmpty { null }

    private fun fromJson(o: JSONObject) {
        state = o.optString("state", "idle")
        val arr = o.optJSONArray("items") ?: JSONArray()
        items = ArrayList<Item>().also { l ->
            for (i in 0 until arr.length()) {
                val it = arr.getJSONObject(i)
                l.add(Item(it.getString("id"), it.getString("category"), it.optString("created_at", "")))
            }
        }
        nextIndex = o.optInt("nextIndex", 0)
        results = o.optJSONArray("results") ?: JSONArray()
        deleted = o.optInt("deleted", 0)
        failed = o.optInt("failed", 0)
        userId = o.optString("userId", "")
        username = o.optString("username", "")
        clientId = o.optString("clientId", "")
        accessToken = o.optString("accessToken", "")
        refreshToken = optStr(o, "refreshToken")
        expiresAt = o.optLong("expiresAt", 0L)
        tokensUpdated = o.optBoolean("tokensUpdated", false)
        windowUse.clear()
        o.optJSONObject("windowUse")?.let { w ->
            w.keys().forEach { k ->
                val a = w.getJSONArray(k)
                windowUse[k] = ArrayList<Long>().also { l -> for (i in 0 until a.length()) l.add(a.getLong(i)) }
            }
        }
        blockedUntil.clear()
        o.optJSONObject("blockedUntil")?.let { b -> b.keys().forEach { k -> blockedUntil[k] = b.getLong(k) } }
        paused = o.optBoolean("paused", false)
        pauseReason = optStr(o, "pauseReason")
        cancelled = o.optBoolean("cancelled", false)
        errorMsg = optStr(o, "error")
    }
}
