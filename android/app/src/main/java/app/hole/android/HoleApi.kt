package app.hole.android

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.net.UnknownHostException
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

/**
 * The three things the app asks of the Hole server, each with the bearer token (the same
 * JOBSEARCH_API_TOKEN the desktop app uses): how the pipeline stands, start a run, and take in
 * what was captured. The pipeline itself runs on the server; nothing here fetches or applies.
 * Plain java.net, so it runs (and is tested) without Android.
 */
class HoleApi(private val baseUrl: String, private val token: String, private val timeoutMs: Int = 20_000) {

    class ApiException(message: String, val code: Int = 0) : Exception(message)

    data class RunStart(val started: Boolean, val running: Boolean)

    data class Ingested(
        val newJobs: Int = 0,
        val knownJobs: Int = 0,
        val newCompetitions: Int = 0,
        val knownCompetitions: Int = 0,
        val rejected: Int = 0,
    ) {
        operator fun plus(other: Ingested) = Ingested(
            newJobs + other.newJobs,
            knownJobs + other.knownJobs,
            newCompetitions + other.newCompetitions,
            knownCompetitions + other.knownCompetitions,
            rejected + other.rejected,
        )

        val new: Int get() = newJobs + newCompetitions
        val known: Int get() = knownJobs + knownCompetitions
    }

    fun status(): JSONObject = call("/api/status", "GET", null).second

    fun startRun(): RunStart {
        val (code, body) = call("/api/run", "POST", ByteArray(0))
        return RunStart(started = code == 202 && body.optBoolean("started"), running = body.optBoolean("running"))
    }

    /** Send reviewed items (at most INGEST_MAX_ITEMS per request on the server, so in chunks). */
    fun ingest(source: String, items: List<InboxItem>): Ingested {
        var total = Ingested()
        for (chunk in items.chunked(CHUNK)) {
            val payload = JSONObject().put("source", source).put("items", JSONArray(chunk.map { payloadOf(it) }))
            val body = call("/api/ingest", "POST", payload.toString().toByteArray(Charsets.UTF_8)).second
            total += Ingested(
                newJobs = body.optJSONObject("jobs")?.optInt("new") ?: 0,
                knownJobs = body.optJSONObject("jobs")?.optInt("duplicate") ?: 0,
                newCompetitions = body.optJSONObject("competitions")?.optInt("new") ?: 0,
                knownCompetitions = body.optJSONObject("competitions")?.optInt("duplicate") ?: 0,
                rejected = body.optInt("rejected"),
            )
        }
        return total
    }

    private fun payloadOf(item: InboxItem): JSONObject = JSONObject()
        .put("kind", if (item.kind == COMPETITION) COMPETITION else JOB)
        .put("title", item.title)
        .put("company", item.company)
        .put("location", item.location)
        .put("url", item.url)
        .put("description", item.description)
        .put("deadline", item.deadline)

    private fun call(path: String, method: String, body: ByteArray?): Pair<Int, JSONObject> {
        if (baseUrl.isBlank()) throw ApiException("Set the Hole address in Settings first")
        if (token.isBlank()) throw ApiException("Set the API token in Settings first")
        val connection = try {
            URI(baseUrl).resolve(path).toURL().openConnection() as HttpURLConnection
        } catch (e: Exception) {
            throw ApiException("The Hole address is not a usable web address")
        }
        try {
            connection.requestMethod = method
            connection.connectTimeout = timeoutMs
            connection.readTimeout = timeoutMs
            connection.setRequestProperty("Authorization", "Bearer $token")
            connection.setRequestProperty("Accept", "application/json")
            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.setFixedLengthStreamingMode(body.size)
                connection.outputStream.use { it.write(body) }
            }
            val code = connection.responseCode
            val stream = if (code >= 400) connection.errorStream else connection.inputStream
            val text = stream?.use { it.readBytes().toString(Charsets.UTF_8) }.orEmpty()
            val json = try {
                JSONObject(text)
            } catch (_: JSONException) {
                JSONObject()
            }
            // 409 on a run is the server saying one is already going: an answer, not a failure.
            if (code == 409 && path == "/api/run") return code to json
            if (code >= 400) throw ApiException(explain(code, json.str("error")), code)
            return code to json
        } catch (e: ApiException) {
            throw e
        } catch (e: UnknownHostException) {
            throw ApiException("No connection: ${e.message ?: "unknown host"}")
        } catch (e: IOException) {
            throw ApiException("Cannot reach the server: ${e.message ?: e.javaClass.simpleName}")
        } finally {
            connection.disconnect()
        }
    }

    companion object {
        /** The server's own limit is 200 items a request. */
        const val CHUNK = 100

        fun explain(code: Int, serverMessage: String?): String = when (code) {
            401 -> "The server refused the token. Paste it again in Settings."
            404 -> "The server has no app API here. Check the address, and that JOBSEARCH_API_TOKEN is set on the server."
            else -> serverMessage ?: "Hole answered HTTP $code"
        }
    }
}
