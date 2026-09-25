package com.waysproperty.tweetdelete

import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicInteger

class DeletionEngineTest {

    private lateinit var server: MockWebServer
    private lateinit var dir: File
    private val requests = CopyOnWriteArrayList<String>()

    @Before fun setUp() {
        server = MockWebServer()
        dir = Files.createTempDirectory("td").toFile()
    }

    @After fun tearDown() {
        server.shutdown()
        dir.deleteRecursively()
    }

    private fun engine() = DeletionEngine(dir, {}, {}, server.url("").toString().trimEnd('/'))

    private fun payload(ids: List<String>, expiresAt: Long = System.currentTimeMillis() + 3_600_000) = JSONObject()
        .put("targets", JSONArray().also { a -> ids.forEach { a.put(JSONObject().put("id", it).put("category", "posts").put("created_at", "2020-01-01T00:00:00.000Z")) } })
        .put("userId", "42").put("username", "tester").put("clientId", "cid")
        .put("tokens", JSONObject().put("access_token", "AT1").put("refresh_token", "RT1").put("expires_at", expiresAt))
        .toString()

    private fun waitFinished(e: DeletionEngine, timeoutMs: Long): JSONObject {
        val end = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < end) {
            val s = JSONObject(e.statusJson())
            if (s.getString("state") == "finished") return s
            Thread.sleep(100)
        }
        throw AssertionError("Run did not finish: ${e.statusJson()}")
    }

    @Test fun rateLimit429IsWaitedOutAndRetriedNotCountedAsFailure() {
        val n = AtomicInteger()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests.add("${request.method} ${request.path}")
                return if (n.incrementAndGet() == 2) {
                    val reset = System.currentTimeMillis() / 1000 + 1
                    MockResponse().setResponseCode(429).addHeader("x-rate-limit-reset", reset.toString())
                        .addHeader("x-rate-limit-remaining", "0").setBody("{\"title\":\"Too Many Requests\"}")
                } else MockResponse().setBody("{\"data\":{\"deleted\":true}}")
            }
        }
        val e = engine()
        val t0 = System.currentTimeMillis()
        assertEquals(null, e.start(payload(listOf("1", "2", "3"))))
        val s = waitFinished(e, 20_000)
        assertEquals(3, s.getInt("deleted"))
        assertEquals(0, s.getInt("failed"))
        assertEquals(4, requests.size) // 1, 2 (429), 2 again, 3
        assertTrue("should have waited for reset", System.currentTimeMillis() - t0 >= 2_000)
        assertTrue(requests.all { it.startsWith("DELETE /2/tweets/") })
    }

    @Test fun expiredTokenIsRefreshedAndRotatedTokenHandedBack() {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests.add("${request.method} ${request.path} ${request.getHeader("Authorization")}")
                return if (request.path == "/2/oauth2/token") {
                    val body = request.body.readUtf8()
                    assertTrue(body.contains("refresh_token=RT1"))
                    MockResponse().setBody("{\"access_token\":\"AT2\",\"refresh_token\":\"RT2\",\"expires_in\":7200}")
                } else MockResponse().setBody("{\"data\":{\"deleted\":true}}")
            }
        }
        val e = engine()
        e.start(payload(listOf("1"), expiresAt = System.currentTimeMillis() - 1000))
        val s = waitFinished(e, 10_000)
        assertEquals(1, s.getInt("deleted"))
        assertTrue(requests[0].startsWith("POST /2/oauth2/token"))
        assertTrue(requests[1].endsWith("Bearer AT2"))
        val t = JSONObject(e.takeUpdatedTokens())
        assertEquals("RT2", t.getString("refresh_token"))
        assertEquals("", e.takeUpdatedTokens()) // only handed back once
    }

    @Test fun failuresAreRecordedWithReason() {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest) =
                MockResponse().setResponseCode(403).setBody("{\"errors\":[{\"message\":\"You are not allowed\"}]}")
        }
        val e = engine()
        e.start(payload(listOf("1", "2")))
        val s = waitFinished(e, 10_000)
        assertEquals(2, s.getInt("failed"))
        val r = JSONArray(e.resultsJson(0)).getJSONObject(0)
        assertEquals("HTTP 403 — You are not allowed", r.getString("detail"))
    }

    @Test fun interruptedRunResumesFromSavedPosition() {
        // Simulate a process death: first engine gets through item 1 then the
        // server stalls; we throw that engine away and restore a new one.
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests.add(request.path!!)
                return MockResponse().setBody("{\"data\":{\"deleted\":true}}")
            }
        }
        val first = engine()
        first.start(payload(listOf("1", "2", "3")))
        waitFinished(first, 10_000)
        // Rewrite the saved state as if it had died after item 1.
        val f = File(dir, "run_state.json")
        val saved = JSONObject(f.readText())
        saved.put("state", "running").put("nextIndex", 1).put("deleted", 1)
            .put("results", JSONArray().put(saved.getJSONArray("results").get(0)))
        f.writeText(saved.toString())
        requests.clear()

        val second = engine()
        second.restore()
        val s = waitFinished(second, 10_000)
        assertEquals(3, s.getInt("deleted"))
        assertEquals(listOf("/2/tweets/2", "/2/tweets/3"), requests.toList())
    }

    @Test fun repeatedRefusalAfterWaitIsReportedAsXDelay() {
        val n = AtomicInteger()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                // Refuse twice (the second time after our wait has ended), then accept.
                return if (n.incrementAndGet() <= 2) {
                    val reset = System.currentTimeMillis() / 1000 + 1
                    MockResponse().setResponseCode(429).addHeader("x-rate-limit-reset", reset.toString())
                } else MockResponse().setBody("{\"data\":{\"deleted\":true}}")
            }
        }
        val kinds = CopyOnWriteArrayList<String>()
        lateinit var e: DeletionEngine
        e = DeletionEngine(dir, {
            val s = JSONObject(e.statusJson())
            if (s.optLong("waitUntil") > 0) kinds.add(s.getString("waitKind"))
            if (s.optBoolean("awaitingX") && s.optLong("waitUntil") == 0L) kinds.add("awaiting")
        }, {}, server.url("").toString().trimEnd('/'))
        e.start(payload(listOf("1")))
        val s = waitFinished(e, 30_000)
        assertEquals(1, s.getInt("deleted"))
        val distinct = kinds.distinct()
        assertEquals(listOf("limit", "awaiting", "xdelay"), distinct)
        assertEquals(false, s.getBoolean("awaitingX"))
    }

    @Test fun cancelStopsTheRun() {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                Thread.sleep(300)
                return MockResponse().setBody("{\"data\":{\"deleted\":true}}")
            }
        }
        val e = engine()
        e.start(payload((1..20).map { it.toString() }))
        Thread.sleep(700)
        e.cancel()
        val s = waitFinished(e, 10_000)
        assertTrue(s.getBoolean("cancelled"))
        assertTrue(s.getInt("deleted") in 1..5)
    }
}
