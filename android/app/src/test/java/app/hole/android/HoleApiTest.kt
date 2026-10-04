package app.hole.android

import java.io.BufferedInputStream
import java.io.IOException
import java.io.InputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CopyOnWriteArrayList
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/**
 * HoleApi against a real HTTP server on loopback that behaves as Hole's does (jobsearch/web/server.py).
 * The server is a few lines of sockets rather than com.sun.net.httpserver, which not every JDK
 * has (Android Studio's trimmed one does not).
 */
class HoleApiTest {
    private class Request(val method: String, val path: String, val headers: Map<String, String>, val body: String)

    private class Response(val code: Int, val body: String)

    private class FakeServer(private val handler: (Request) -> Response) : AutoCloseable {
        private val socket = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
        val port: Int get() = socket.localPort

        init {
            Thread {
                while (!socket.isClosed) {
                    val client = try {
                        socket.accept()
                    } catch (_: IOException) {
                        break
                    }
                    Thread { serve(client) }.apply { isDaemon = true }.start()
                }
            }.apply { isDaemon = true }.start()
        }

        private fun line(input: InputStream): String? {
            val text = StringBuilder()
            while (true) {
                val next = input.read()
                if (next < 0) return if (text.isEmpty()) null else text.toString()
                if (next == '\n'.code) return text.toString().trimEnd('\r')
                text.append(next.toChar())
            }
        }

        private fun serve(client: Socket) = client.use {
            val input = BufferedInputStream(it.getInputStream())
            val first = line(input)?.split(" ") ?: return@use
            val headers = HashMap<String, String>()
            while (true) {
                val header = line(input)
                if (header.isNullOrEmpty()) break
                headers[header.substringBefore(':').trim().lowercase()] = header.substringAfter(':').trim()
            }
            val body = String(input.readNBytes(headers["content-length"]?.toIntOrNull() ?: 0), Charsets.UTF_8)
            val response = handler(Request(first[0], first[1], headers, body))
            val bytes = response.body.toByteArray(Charsets.UTF_8)
            val out = it.getOutputStream()
            out.write("HTTP/1.1 ${response.code} Reply\r\nContent-Type: application/json\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n".toByteArray())
            out.write(bytes)
            out.flush()
        }

        override fun close() = socket.close()
    }

    private val token = "s3cret-token"
    private val requests = CopyOnWriteArrayList<Request>()
    private var apiOff = false
    private var runsStarted = 0
    private lateinit var server: FakeServer

    /** What the real handler does: no token configured is a 404, a wrong or missing bearer token a 401. */
    private fun hole(request: Request): Response {
        requests += request
        if (apiOff) return Response(404, """{"error":"not found"}""")
        if (request.headers["authorization"] != "Bearer $token") return Response(401, """{"error":"bad token"}""")
        return when (request.path) {
            "/api/status" -> Response(200, """{"running":false,"run":null,"run_at":"12:00","timezone":"GMT","auto_apply":false,"counts":{"remaining":7,"applied":2}}""")
            "/api/run" -> {
                runsStarted += 1
                if (runsStarted == 1) Response(202, """{"started":true,"running":true}""")
                else Response(409, """{"started":false,"running":true,"error":"a run is already going"}""")
            }
            "/api/ingest" -> {
                val items = JSONObject(request.body).getJSONArray("items")
                val jobs = (0 until items.length()).count { items.getJSONObject(it).getString("kind") == "job" }
                Response(200, """{"jobs":{"new":$jobs,"duplicate":1},"competitions":{"new":${items.length() - jobs},"duplicate":0},"rejected":0}""")
            }
            else -> Response(404, """{"error":"not found"}""")
        }
    }

    @Before
    fun start() {
        server = FakeServer { hole(it) }
    }

    @After
    fun stop() = server.close()

    private fun api(withToken: String = token) = HoleApi("http://127.0.0.1:${server.port}", withToken, timeoutMs = 5_000)

    private fun job(n: Int) = InboxItem(kind = JOB, title = "Intern $n", company = "Acme", location = "Pune", url = "https://x/$n", service = "linkedin")

    @Test
    fun statusIsAGetWithTheBearerToken() {
        val status = api().status()
        assertFalse(status.getBoolean("running"))
        assertEquals("GET", requests.single().method)
        assertEquals("Bearer $token", requests.single().headers["authorization"])
        assertEquals("Not run yet · next 12:00 GMT · 7 remaining, 2 applied", Status.describe(status).text)
    }

    @Test
    fun aWrongTokenIsRefusedWithAMessageThatSaysWhatToDo() {
        try {
            api("wrong").status()
            fail("expected a refusal")
        } catch (e: HoleApi.ApiException) {
            assertEquals(401, e.code)
            assertTrue(e.message!!, e.message!!.contains("refused the token"))
        }
    }

    @Test
    fun aServerWithNoApiTokenSetAnswers404AndTheMessageSaysWhereToLook() {
        apiOff = true
        try {
            api().status()
            fail("expected a 404")
        } catch (e: HoleApi.ApiException) {
            assertEquals(404, e.code)
            assertTrue(e.message!!, e.message!!.contains("JOBSEARCH_API_TOKEN"))
        }
    }

    @Test
    fun aServerErrorSurfacesItsOwnMessage() {
        assertEquals("a run is already going", HoleApi.explain(409, "a run is already going"))
        assertEquals("Hole answered HTTP 500", HoleApi.explain(500, null))
        assertEquals("at most 200 items per request", HoleApi.explain(413, "at most 200 items per request"))
    }

    @Test
    fun startingARunIsAPostAndAnotherWhileOneGoesIsAnAnswerNotAnError() {
        val first = api().startRun()
        assertTrue(first.started)
        assertTrue(first.running)
        assertEquals("POST", requests[0].method)
        assertEquals("0", requests[0].headers["content-length"])

        val second = api().startRun()
        assertFalse(second.started)
        assertTrue(second.running)
    }

    @Test
    fun ingestPostsTheReviewedItemsWithTheirSourceAsJson() {
        val result = api().ingest("linkedin", listOf(job(1), InboxItem(kind = COMPETITION, title = "Spring Hack", deadline = "2026-11-01", url = "https://x/h", service = "linkedin")))
        assertEquals(1, result.newJobs)
        assertEquals(1, result.newCompetitions)
        assertEquals(1, result.knownJobs)

        val seen = requests.single()
        assertEquals("POST", seen.method)
        assertTrue(seen.headers["content-type"]!!.startsWith("application/json"))
        val sent = JSONObject(seen.body)
        assertEquals("linkedin", sent.getString("source"))
        val first = sent.getJSONArray("items").getJSONObject(0)
        assertEquals(listOf("job", "Intern 1", "Acme", "Pune", "https://x/1"), listOf("kind", "title", "company", "location", "url").map { first.getString(it) })
        assertEquals("2026-11-01", sent.getJSONArray("items").getJSONObject(1).getString("deadline"))
    }

    @Test
    fun ingestSendsALongListInChunksTheServerWillAccept() {
        val result = api().ingest("android", (1..250).map { job(it) })
        assertEquals(3, requests.size)
        assertEquals(listOf(100, 100, 50), requests.map { JSONObject(it.body).getJSONArray("items").length() })
        assertEquals(250, result.newJobs)
        assertEquals(3, result.knownJobs)
    }

    @Test
    fun anUnreachableServerIsAMessageNotACrash() {
        val port = server.port
        server.close()
        try {
            HoleApi("http://127.0.0.1:$port", token, timeoutMs = 2_000).status()
            fail("expected a failure")
        } catch (e: HoleApi.ApiException) {
            assertTrue(e.message!!, e.message!!.startsWith("Cannot reach the server"))
        }
    }

    @Test
    fun missingSettingsAreCaughtBeforeAnythingIsSent() {
        for (bad in listOf(HoleApi("", token), HoleApi("http://127.0.0.1:1", ""))) {
            try {
                bad.status()
                fail("expected a refusal to try")
            } catch (e: HoleApi.ApiException) {
                assertTrue(e.message!!, e.message!!.contains("Settings"))
            }
        }
        assertTrue(requests.isEmpty())
    }

    @Test
    fun aMalformedAddressIsAMessageNotACrash() {
        try {
            HoleApi("http://exa mple.com", token).status()
            fail("expected a failure")
        } catch (e: HoleApi.ApiException) {
            assertTrue(e.message!!, e.message!!.contains("usable web address"))
        }
    }
}
