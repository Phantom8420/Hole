package app.hole.android

/**
 * Turns what another app shares (the LinkedIn, Indeed or Discord app, Chrome...) into inbox items
 * for you to review. Only the text and link the app chose to hand over are read; nothing is
 * fetched. Titles are best effort, which is why the inbox lets you correct them before sending.
 */
object ShareParser {
    private val LINK = Regex("""https?://[^\s<>"']+""")
    private val SPACES = Regex("""\s+""")
    private val SEPARATORS = charArrayOf(' ', '-', '–', '—', ':', '|', '•', '·', '>', ' ')
    private val TRAILING = charArrayOf('.', ',', ';', ':', '!', '?', ')', ']', '}', '\'', '"')
    private val SITE_TAIL = Regex(
        """\s*[|·•\-–—]\s*(?:LinkedIn|Indeed(?:\.com)?|Glassdoor|Handshake|Wellfound|Devpost|Devfolio|Unstop|MLH|Major League Hacking)\s*$""",
        RegexOption.IGNORE_CASE,
    )

    // "Check out this job at Acme: Data Intern", the words LinkedIn puts in front of a shared job.
    private val LINKEDIN = Regex("""^check out this (?:job|role|position|opening)(?: at ([^:]+))?:\s*(.+)$""", RegexOption.IGNORE_CASE)
    private val AT = Regex("""\s+(?:at|@)\s+""", RegexOption.IGNORE_CASE)
    private val DASH = Regex("""\s+[-–—|]\s+""")

    // The same words the desktop app's `links` extractor takes for a competition.
    private val COMPETITION_WORDS = Regex(
        """hackathon|datathon|ideathon|competition|contest|challenge|bounty|fellowship|grant|case[- ]?(?:study|comp)""",
        RegexOption.IGNORE_CASE,
    )
    private val COMPETITION_HOSTS = listOf("devpost.com", "devfolio.co", "unstop.com", "mlh.io", "mlh.com", "hackerearth.com", "dorahacks.io", "lablab.ai")

    private const val MIN_WORDS = 4

    /** `subject` and `text` are the two extras an Android share carries; either may be missing. */
    fun parse(subject: String?, text: String?): List<InboxItem> {
        val heading = tidy(subject.orEmpty())
        val lines = text.orEmpty().lines().map { tidy(it) }.filter { it.isNotEmpty() }

        // Each link, with what was said about it: the rest of its line, or failing that the line before.
        val links = ArrayList<Pair<String, String?>>()
        for ((index, line) in lines.withIndex()) {
            for (match in LINK.findAll(line)) {
                links += trimLink(match.value) to said(line.removeRange(match.range), lines.getOrNull(index - 1))
            }
        }
        if (links.isEmpty()) {
            LINK.find(heading)?.let { found -> links += trimLink(found.value) to said(heading.removeRange(found.range), null) }
        }
        if (links.isEmpty()) {
            val words = heading.ifEmpty { lines.firstOrNull().orEmpty() }
            return if (words.isEmpty()) emptyList() else listOf(item(words, ""))
        }

        // Chrome and most apps put the page title in the subject and only the link in the text.
        val fallback = if (links.size == 1 && heading.isNotEmpty() && !LINK.containsMatchIn(heading)) heading else null
        return links.map { (address, said) -> item(said ?: fallback ?: Urls.host(address).orEmpty(), address) }
    }

    private fun tidy(text: String): String = text.replace(SPACES, " ").trim()

    private fun trimLink(raw: String): String = raw.trimEnd(*TRAILING)

    private fun said(rest: String, previous: String?): String? {
        val words = rest.trim(*SEPARATORS)
        return when {
            words.length >= MIN_WORDS -> words
            previous != null && previous.length >= MIN_WORDS && !LINK.containsMatchIn(previous) -> previous
            else -> null
        }
    }

    private fun item(words: String, address: String): InboxItem {
        val (title, company, location) = split(words.replace(SITE_TAIL, "").trim('"', '“', '”', ' '))
        val competition = isCompetition(address, title)
        return InboxItem(
            kind = if (competition) COMPETITION else JOB,
            title = title.take(300),
            company = if (competition) "" else company.take(200),
            location = if (competition) "" else location.take(200),
            url = address,
            service = SHARE,
        )
    }

    /** "Title at Company - Place", "Title - Company - Place", or just a title. */
    private fun split(words: String): Triple<String, String, String> {
        LINKEDIN.matchEntire(words)?.let { return Triple(it.groupValues[2].trim(), it.groupValues[1].trim(), "") }
        val at = AT.find(words)
        if (at != null) {
            val rest = words.substring(at.range.last + 1).split(DASH)
            return Triple(words.substring(0, at.range.first).trim(), rest[0].trim(), rest.getOrNull(1)?.trim().orEmpty())
        }
        val parts = words.split(DASH)
        if (parts.size >= 2) return Triple(parts[0].trim(), parts[1].trim(), parts.getOrNull(2)?.trim().orEmpty())
        return Triple(words, "", "")
    }

    private fun isCompetition(address: String, title: String): Boolean {
        val host = Urls.host(address).orEmpty()
        return COMPETITION_HOSTS.any { host == it || host.endsWith(".$it") } ||
            COMPETITION_WORDS.containsMatchIn(title) ||
            COMPETITION_WORDS.containsMatchIn(address)
    }
}
