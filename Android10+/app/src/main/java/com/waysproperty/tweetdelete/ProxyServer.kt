package com.waysproperty.tweetdelete

import android.content.Context
import fi.iki.elonen.NanoHTTPD
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.ByteArrayOutputStream
import java.net.ServerSocket
import java.util.concurrent.TimeUnit

/**
 * Android equivalent of server.py from the desktop build. It does exactly the
 * same two jobs, for exactly the same two reasons:
 *
 * 1. Serves the bundled web UI (HTML/CSS/JS) from the APK's assets/public/,
 *    so the WebView always loads from a real http:// origin rather than
 *    file://, which X's OAuth redirect_uri rules disallow.
 * 2. Reverse-proxies /api/x/<path> to https://api.x.com/<path>. This isn't
 *    strictly required for CORS on Android the way it was for the desktop
 *    browser build (CORS is a browser same-origin rule; native OkHttp calls
 *    are never subject to it) — but keeping the same proxy shape means the
 *    existing api.js / oauth.js code needs zero changes, and it still serves
 *    a second real purpose: it's what hosts the OAuth loopback callback at
 *    http://127.0.0.1:<port>/callback.html, which X requires (see
 *    https://docs.x.com/fundamentals/developer-apps — custom URL schemes are
 *    explicitly disallowed for the redirect_uri; only https or the
 *    127.0.0.1 loopback pattern from RFC 8252 are accepted for a Native App).
 */
class ProxyServer(private val context: Context, port: Int) : NanoHTTPD(port) {

    companion object {
        private const val UPSTREAM = "https://api.x.com"
        private const val DEFAULT_PORT = 8765

        // Same free-port probing behavior as the desktop server.py, and same
        // default port, so a Client ID/redirect URI already registered for
        // the desktop build also works unmodified on Android.
        fun findFreePort(preferred: Int = DEFAULT_PORT, attempts: Int = 20): Int {
            var port = preferred
            repeat(attempts) {
                try {
                    ServerSocket(port).close()
                    return port
                } catch (_: Exception) {
                    port++
                }
            }
            throw IllegalStateException("No free port found near $preferred")
        }
    }

    private val http = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    private val hopByHop = setOf(
        "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length"
    )

    override fun serve(session: IHTTPSession): Response {
        val uri = session.uri
        val response = if (uri.startsWith("/api/x/")) {
            proxy(session)
        } else {
            serveAsset(uri)
        }
        // Force every response to close its connection rather than let
        // NanoHTTPD keep it alive for a pipelined next request. This is the
        // actual root cause behind delete calls failing with a stale,
        // unrelated "tweet.fields" error from the *previous* GET request:
        // NanoHTTPD reuses one session object across keep-alive requests on
        // the same connection, and doesn't fully clear its parsed query
        // string when the next request on that connection has none of its
        // own - so a DELETE with no query string was inheriting the query
        // string left over from the GET call just before it. Forcing a
        // fresh connection per request (negligible cost on loopback)
        // guarantees a fresh, correctly-initialized session every time.
        response.closeConnection(true)
        return response
    }

    /**
     * NanoHTTPD's built-in Status enum only covers common codes. If X
     * returns something outside that set (X's pay-per-use billing errors
     * reportedly use 402 Payment Required, which is NOT in NanoHTTPD's
     * enum), the old code fell back to plain OK (200) here — meaning a
     * genuine failure from X would get silently relayed to the WebView as
     * a fake success. This preserves the real code no matter what it is.
     */
    private class RawStatus(private val code: Int) : Response.IStatus {
        override fun getDescription(): String = "$code Upstream"
        override fun getRequestStatus(): Int = code
    }

    private fun proxy(session: IHTTPSession): Response {
        val upstreamPath = session.uri.removePrefix("/api/x")
        val query = session.queryParameterString?.let { if (it.isNotEmpty()) "?$it" else "" } ?: ""
        val url = UPSTREAM + upstreamPath + query

        return try {
            val builder = Request.Builder().url(url)

            for ((key, value) in session.headers) {
                if (key.lowercase() !in hopByHop) builder.addHeader(key, value)
            }

            // Forward whatever method the WebView actually sent, explicitly,
            // for every verb X's API uses here (GET, POST for the OAuth
            // token exchange, DELETE for deletions). The previous version
            // only special-cased POST/PUT/DELETE and silently left anything
            // else (including a same-origin fetch quirk sending OPTIONS) on
            // OkHttp's implicit default of GET — which would have forwarded
            // it to api.x.com as the wrong method entirely.
            val method = session.method.name
            when (method) {
                "GET" -> builder.method("GET", null)
                "HEAD" -> builder.method("HEAD", null)
                "DELETE" -> {
                    val bodyBytes = readRequestBody(session)
                    val contentType = session.headers["content-type"]?.toMediaTypeOrNull()
                    builder.method("DELETE", if (bodyBytes.isEmpty()) null else bodyBytes.toRequestBody(contentType))
                }
                else -> { // POST, PUT, and anything else that can carry a body.
                    val bodyBytes = readRequestBody(session)
                    val contentType = session.headers["content-type"]
                        ?.toMediaTypeOrNull() ?: "application/x-www-form-urlencoded".toMediaTypeOrNull()
                    builder.method(method, bodyBytes.toRequestBody(contentType))
                }
            }

            http.newCall(builder.build()).execute().use { resp ->
                val bytes = resp.body?.bytes() ?: ByteArray(0)
                val contentType = resp.header("Content-Type") ?: "application/json"
                val status: Response.IStatus = Response.Status.lookup(resp.code) ?: RawStatus(resp.code)
                val r = newFixedLengthResponse(
                    status,
                    contentType,
                    bytes.inputStream(),
                    bytes.size.toLong()
                )
                for (name in resp.headers.names()) {
                    if (name.lowercase() !in hopByHop && !name.equals("Content-Type", true)) {
                        resp.headers(name).forEach { v -> r.addHeader(name, v) }
                    }
                }
                r
            }
        } catch (e: Exception) {
            newFixedLengthResponse(
                Response.Status.INTERNAL_ERROR,
                "application/json",
                """{"error":"proxy_failed","detail":${'"'}${e.message?.replace("\"", "'") ?: "unknown"}${'"'}}"""
            )
        }
    }

    private fun readRequestBody(session: IHTTPSession): ByteArray {
        val len = session.headers["content-length"]?.toIntOrNull() ?: 0
        if (len <= 0) return ByteArray(0)
        val buffer = ByteArrayOutputStream()
        val chunk = ByteArray(8192)
        var remaining = len
        val input = session.inputStream
        while (remaining > 0) {
            val read = input.read(chunk, 0, minOf(chunk.size, remaining))
            if (read <= 0) break
            buffer.write(chunk, 0, read)
            remaining -= read
        }
        return buffer.toByteArray()
    }

    private fun serveAsset(uriIn: String): Response {
        var path = uriIn
        if (path == "/" || path.isEmpty()) path = "/index.html"
        val assetPath = "public" + path

        return try {
            context.assets.open(assetPath).use { input ->
                val bytes = input.readBytes()
                newFixedLengthResponse(Response.Status.OK, mimeTypeFor(path), bytes.inputStream(), bytes.size.toLong())
            }
        } catch (e: Exception) {
            newFixedLengthResponse(Response.Status.NOT_FOUND, "text/plain", "Not found: $path")
        }
    }

    private fun mimeTypeFor(path: String): String = when {
        path.endsWith(".html") -> "text/html; charset=utf-8"
        path.endsWith(".js") -> "application/javascript; charset=utf-8"
        path.endsWith(".css") -> "text/css; charset=utf-8"
        path.endsWith(".json") -> "application/json; charset=utf-8"
        path.endsWith(".png") -> "image/png"
        path.endsWith(".svg") -> "image/svg+xml"
        path.endsWith(".ico") -> "image/x-icon"
        else -> "application/octet-stream"
    }
}
