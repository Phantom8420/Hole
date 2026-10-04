package app.hole.android

import java.net.URI

/** Addresses, judged the way the desktop app judges them (desktop/src/services.js: webUrl, sameHost). */
object Urls {
    const val DEFAULT_HOLE = "https://hole-roan.vercel.app"

    private fun parse(text: String): URI? = try {
        URI(text.trim())
    } catch (_: Exception) {
        null
    }

    /** A page address typed for a service: http or https, with a host. Null for anything else. */
    fun web(input: String): String? {
        val text = input.trim()
        val uri = parse(if (text.contains("://")) text else "https://$text") ?: return null
        val scheme = uri.scheme?.lowercase()
        return if ((scheme == "https" || scheme == "http") && !uri.host.isNullOrEmpty()) uri.toString() else null
    }

    /**
     * The address of a Hole server as typed, reduced to its origin. https only: the app sends
     * a bearer token and a session cookie there, and Android refuses cleartext traffic anyway.
     */
    fun holeOrigin(input: String): String? {
        val text = input.trim()
        if (text.isEmpty()) return null
        val uri = parse(if (text.contains("://")) text else "https://$text") ?: return null
        val host = uri.host ?: return null
        if (uri.scheme?.lowercase() != "https") return null
        val port = if (uri.port == -1 || uri.port == 443) "" else ":${uri.port}"
        return "https://${host.lowercase()}$port"
    }

    fun host(url: String?): String? = url?.let { parse(it)?.host?.lowercase() }

    fun sameHost(a: String?, b: String?): Boolean {
        val first = host(a)
        return first != null && first == host(b)
    }
}
