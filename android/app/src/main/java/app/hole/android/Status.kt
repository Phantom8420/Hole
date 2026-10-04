package app.hole.android

import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneOffset
import org.json.JSONObject

/**
 * How the pipeline stands, in one line. Worded exactly as the desktop app words it
 * (desktop/src/pipeline.js), so both say the same thing about the same run.
 */
object Status {
    data class Line(val text: String, val running: Boolean)

    /** A stamp from the server: ISO 8601, with or without an offset (none means GMT). */
    fun parseInstant(stamp: String?): Instant? {
        if (stamp.isNullOrBlank()) return null
        val text = stamp.trim().replace(' ', 'T')
        return try {
            OffsetDateTime.parse(text).toInstant()
        } catch (_: Exception) {
            try {
                LocalDateTime.parse(text).toInstant(ZoneOffset.UTC)
            } catch (_: Exception) {
                null
            }
        }
    }

    fun ago(stamp: String?, now: Instant): String {
        val then = parseInstant(stamp) ?: return ""
        val minutes = maxOf(0L, Math.round((now.toEpochMilli() - then.toEpochMilli()) / 60000.0))
        return when {
            minutes < 2 -> "just now"
            minutes < 120 -> "$minutes min ago"
            minutes < 48 * 60 -> "${Math.round(minutes / 60.0)} h ago"
            else -> "${Math.round(minutes / 1440.0)} days ago"
        }
    }

    /** What is running, what ran, when next, and what is left. */
    fun describe(status: JSONObject?, now: Instant = Instant.now()): Line {
        val parts = ArrayList<String>()
        val run = status?.optJSONObject("run")
        val running = status?.optBoolean("running", false) == true
        when {
            running -> parts += "Running now" + (run?.let { ", started ${ago(it.str("started_at"), now)}" } ?: "")
            run != null -> {
                val finished = ago(run.str("finished_at") ?: run.str("started_at"), now)
                parts += "Last run $finished: ${run.count("sourced")} new, ${run.count("tailored")} drafted, ${run.count("sent")} sent"
            }
            else -> parts += "Not run yet"
        }
        if (status != null) {
            val runAt = status.str("run_at")
            if (!runAt.isNullOrEmpty()) parts += "next $runAt ${status.str("timezone") ?: "GMT"}"
            status.optJSONObject("counts")?.let { parts += "${it.count("remaining")} remaining, ${it.count("applied")} applied" }
            if (status.optBoolean("auto_apply", false)) parts += "auto-apply on"
        }
        return Line(parts.joinToString(" · "), running)
    }
}
