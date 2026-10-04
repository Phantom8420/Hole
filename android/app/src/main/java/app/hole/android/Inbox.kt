package app.hole.android

import java.util.UUID
import org.json.JSONArray
import org.json.JSONObject

const val JOB = "job"
const val COMPETITION = "competition"

/** Where an item came from when another app shared it, rather than a page captured in a tab. */
const val SHARE = "android"

/** What the server stores for a description at most (it truncates there anyway). */
private const val DESCRIPTION_LIMIT = 20_000

/**
 * Something found, waiting for you to look at it. Mirrors the desktop inbox: nothing leaves the
 * phone until you tick it and press send, and a job needs a title and a company (the server
 * rejects one without).
 */
data class InboxItem(
    val id: String = UUID.randomUUID().toString(),
    var kind: String,
    var title: String,
    var company: String = "",
    var location: String = "",
    var deadline: String = "",
    val url: String = "",
    val description: String = "",
    /** The service tab it was captured in, or SHARE. It becomes the source the server records. */
    val service: String,
    var selected: Boolean = false,
) {
    val complete: Boolean get() = title.isNotBlank() && (kind != JOB || company.isNotBlank())

    fun toJson(): JSONObject = JSONObject()
        .put("id", id)
        .put("kind", kind)
        .put("title", title)
        .put("company", company)
        .put("location", location)
        .put("deadline", deadline)
        .put("url", url)
        .put("description", description)
        .put("service", service)
        .put("selected", selected)

    companion object {
        fun fromJson(o: JSONObject) = InboxItem(
            id = o.str("id") ?: UUID.randomUUID().toString(),
            kind = if (o.str("kind") == COMPETITION) COMPETITION else JOB,
            title = o.str("title").orEmpty(),
            company = o.str("company").orEmpty(),
            location = o.str("location").orEmpty(),
            deadline = o.str("deadline").orEmpty(),
            url = o.str("url").orEmpty(),
            description = o.str("description").orEmpty(),
            service = o.str("service") ?: SHARE,
            selected = o.optBoolean("selected", false),
        )

        /** One item the page extractors returned (see assets/extractors.js); a plain page is for you to classify, so a job. */
        fun fromCapture(raw: JSONObject, service: String) = InboxItem(
            kind = if (raw.str("kind") == COMPETITION) COMPETITION else JOB,
            title = raw.str("title").orEmpty().trim(),
            company = raw.str("company").orEmpty().trim(),
            location = raw.str("location").orEmpty().trim(),
            deadline = raw.str("deadline").orEmpty().trim(),
            url = raw.str("url").orEmpty().trim(),
            description = raw.str("description").orEmpty().take(DESCRIPTION_LIMIT),
            service = service,
        )
    }
}

class Inbox(initial: List<InboxItem> = emptyList()) {
    val items: MutableList<InboxItem> = initial.toMutableList()

    /**
     * Add what was found, skipping anything whose address is already here (as the desktop inbox
     * does), and tick the ones that are ready to send. Returns how many were new.
     */
    fun add(found: List<InboxItem>): Int {
        val known = items.map { it.url }.filter { it.isNotBlank() }.toMutableSet()
        var added = 0
        for (item in found) {
            if (item.url.isNotBlank() && !known.add(item.url)) continue
            item.selected = item.complete
            items += item
            added += 1
        }
        return added
    }

    fun clear() = items.clear()

    fun removeAll(sent: Collection<InboxItem>) {
        val ids = sent.map { it.id }.toSet()
        items.removeAll { it.id in ids }
    }

    fun toJson(): String = JSONObject().put("items", JSONArray(items.map { it.toJson() })).toString()

    companion object {
        /** An inbox from its saved form; anything unreadable is an empty inbox, never a crash. */
        fun fromJson(text: String?): Inbox {
            if (text.isNullOrBlank()) return Inbox()
            return try {
                val rows = JSONObject(text).optJSONArray("items") ?: return Inbox()
                Inbox((0 until rows.length()).mapNotNull { index -> rows.optJSONObject(index)?.let { InboxItem.fromJson(it) } })
            } catch (_: Exception) {
                Inbox()
            }
        }
    }
}
