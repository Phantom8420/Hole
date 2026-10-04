package app.hole.android

import android.content.Context
import java.io.File

/**
 * The one inbox, shared by every screen and kept in a file so that something shared to Hole while
 * the app is closed is still there when you open it. Held in memory between calls; `save` writes it.
 */
object InboxStore {
    private var cached: Inbox? = null

    private fun file(context: Context) = File(context.applicationContext.filesDir, "inbox.json")

    @Synchronized
    fun get(context: Context): Inbox {
        cached?.let { return it }
        val target = file(context)
        val text = try {
            if (target.exists()) target.readText() else null
        } catch (_: Exception) {
            null
        }
        return Inbox.fromJson(text).also { cached = it }
    }

    @Synchronized
    fun save(context: Context) {
        val inbox = cached ?: return
        val target = file(context)
        val temp = File(target.parentFile, "inbox.json.tmp")
        try {
            temp.writeText(inbox.toJson())
            if (!temp.renameTo(target)) {
                temp.copyTo(target, overwrite = true)
                temp.delete()
            }
        } catch (_: Exception) {
            // Losing a save costs the items captured since the last one, which are one tap to capture again.
        }
    }
}
