package app.hole.android

import org.json.JSONObject

/** A string field, or null when it is missing or JSON null (org.json answers "null" for the latter). */
fun JSONObject.str(key: String): String? = if (isNull(key)) null else optString(key)

/** A whole-number field; a missing or null one counts as 0. */
fun JSONObject.count(key: String): Int = if (isNull(key)) 0 else optInt(key, 0)
