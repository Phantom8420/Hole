package app.hole.android

import org.json.JSONObject

const val HOLE_ID = "hole"

private const val DASHBOARD_ID = "dashboard"
private const val DEFAULT_SECTION = "listings"

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
    /** The id of the [Section] it sits in. */
    val section: String,
    val extractors: List<String>,
) {
    val isHole: Boolean get() = id == HOLE_ID
}

/** One of the places on the bottom bar (Dashboard, Listings, Social): its services show as a strip of tabs. */
data class Section(val id: String, val name: String, val icon: String)

class Catalog(val sections: List<Section>, val services: List<Service>) {
    fun sitesIn(section: Section): List<Service> = services.filter { it.section == section.id }

    fun sectionOf(service: Service): Section = sections.firstOrNull { it.id == service.section } ?: sections.first()

    /** The most the app may do on any service in the place, which is what the dot on its tile shows. */
    fun levelOf(section: Section): Level = sitesIn(section).maxByOrNull { it.level.ordinal }?.level ?: Level.VIEW
}

object Services {
    /**
     * The places and services in assets/services.json, which android/tools/sync.js writes from the
     * desktop app's. As there, Hole is always the Dashboard, a service that names no place (or one
     * that does not exist) is in Listings, and a place with nothing in it is left off.
     */
    fun parse(json: String): Catalog {
        val root = JSONObject(json)
        val places = root.getJSONArray("sections")
        val sections = List(places.length()) {
            val row = places.getJSONObject(it)
            val id = row.getString("id")
            Section(id = id, name = row.str("name") ?: id, icon = row.str("icon") ?: id)
        }

        val rows = root.getJSONArray("services")
        val services = ArrayList<Service>()
        for (i in 0 until rows.length()) {
            val row = rows.getJSONObject(i)
            val id = row.getString("id")
            val level = when (row.str("level")) {
                "capture" -> Level.CAPTURE
                "batch" -> Level.BATCH
                else -> Level.VIEW
            }
            val wanted = row.str("section")
            val section = when {
                id == HOLE_ID -> DASHBOARD_ID
                wanted != null && wanted != DASHBOARD_ID && sections.any { it.id == wanted } -> wanted
                else -> DEFAULT_SECTION
            }
            val names = row.optJSONArray("extractors")
            services += Service(
                id = id,
                name = row.str("name") ?: id,
                glyph = row.str("glyph") ?: "",
                url = row.str("url"),
                level = level,
                section = section,
                extractors = if (names == null) emptyList() else List(names.length()) { names.getString(it) },
            )
        }
        return Catalog(sections.filter { place -> services.any { it.section == place.id } }, services)
    }
}
