package app.hole.android

import org.json.JSONArray

const val HOLE_ID = "hole"

/** How much the app may do on a service, as in desktop/src/services.js. */
enum class Level(val label: String) {
    VIEW("view only"),
    CAPTURE("capture on request"),
    BATCH("scan allowed"),
}

data class Service(
    val id: String,
    val name: String,
    val glyph: String,
    /** Where it opens; null for Hole itself (the address in Settings) and for a service with none yet. */
    val url: String?,
    val level: Level,
    val extractors: List<String>,
) {
    val isHole: Boolean get() = id == HOLE_ID
}

object Services {
    /** The list in assets/services.json, which android/tools/sync.js writes from the desktop app's. */
    fun parse(json: String): List<Service> {
        val rows = JSONArray(json)
        val out = ArrayList<Service>()
        for (i in 0 until rows.length()) {
            val row = rows.getJSONObject(i)
            val level = when (row.str("level")) {
                "capture" -> Level.CAPTURE
                "batch" -> Level.BATCH
                else -> Level.VIEW
            }
            val names = row.optJSONArray("extractors")
            out += Service(
                id = row.getString("id"),
                name = row.str("name") ?: row.getString("id"),
                glyph = row.str("glyph") ?: "",
                url = row.str("url"),
                level = level,
                extractors = if (names == null) emptyList() else List(names.length()) { names.getString(it) },
            )
        }
        return out
    }
}
