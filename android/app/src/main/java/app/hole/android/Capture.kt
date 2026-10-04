package app.hole.android

import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener

/**
 * "Capture page": run the service's extractors (assets/extractors.js, the desktop app's own)
 * inside the page you are looking at, and read back what they found. It runs when you press the
 * button and reads only that page; nothing is sent from here.
 */
object Capture {
    sealed interface Result
    data class Found(val items: List<InboxItem>) : Result
    data class Failed(val message: String) : Result

    /** The script to hand WebView.evaluateJavascript: the library, then a call that returns JSON text. */
    fun script(library: String, names: List<String>): String =
        "(function(){try{" + library + "\nreturn JSON.stringify(holeExtract(" + JSONArray(names) + "));}" +
            "catch(e){return JSON.stringify({error:String((e&&e.message)||e)});}})()"

    /**
     * evaluateJavascript hands back the result as JSON, and the script's result is itself JSON text,
     * so a string in a string: unwrap the outer, parse the inner.
     */
    fun parse(raw: String?, service: String): Result {
        if (raw == null || raw == "null") return Failed("The page gave nothing back")
        return try {
            val inner = JSONTokener(raw).nextValue() as? String ?: return Failed("The page gave nothing back")
            when (val value = JSONTokener(inner).nextValue()) {
                is JSONArray -> Found((0 until value.length()).mapNotNull { value.optJSONObject(it) }.map { InboxItem.fromCapture(it, service) }.filter { it.title.isNotEmpty() })
                is JSONObject -> Failed(value.str("error") ?: "The page could not be read")
                else -> Failed("The page could not be read")
            }
        } catch (e: Exception) {
            Failed("The page could not be read")
        }
    }
}
