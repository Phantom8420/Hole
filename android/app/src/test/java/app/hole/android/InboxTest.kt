package app.hole.android

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class InboxTest {
    private fun job(title: String, company: String = "Acme", url: String = "") = InboxItem(kind = JOB, title = title, company = company, url = url, service = "linkedin")

    @Test
    fun aJobNeedsATitleAndACompanyAndACompetitionOnlyATitle() {
        assertTrue(job("Data Intern").complete)
        assertFalse(job("Data Intern", company = " ").complete)
        assertFalse(job(" ").complete)
        assertTrue(InboxItem(kind = COMPETITION, title = "Hack", service = "devpost").complete)
        assertFalse(InboxItem(kind = COMPETITION, title = "", service = "devpost").complete)
    }

    @Test
    fun addingSkipsAnAddressAlreadyHereAndTicksWhatIsReady() {
        val inbox = Inbox(listOf(job("Seen", url = "https://x/1")))
        val added = inbox.add(listOf(job("Seen again", url = "https://x/1"), job("New", url = "https://x/2"), job("No company", company = "", url = "https://x/3")))
        assertEquals(2, added)
        assertEquals(listOf("Seen", "New", "No company"), inbox.items.map { it.title })
        assertEquals(listOf(false, true, false), inbox.items.map { it.selected })
    }

    @Test
    fun itemsWithoutAnAddressAreNeverTakenForDuplicates() {
        val inbox = Inbox()
        assertEquals(2, inbox.add(listOf(job("A"), job("B"))))
    }

    @Test
    fun removingSentItemsLeavesTheRest() {
        val inbox = Inbox(listOf(job("A"), job("B"), job("C")))
        inbox.removeAll(listOf(inbox.items[0], inbox.items[2]))
        assertEquals(listOf("B"), inbox.items.map { it.title })
    }

    @Test
    fun aSavedInboxComesBackAsItWas() {
        val item = InboxItem(kind = COMPETITION, title = "Spring Hack", deadline = "2026-11-01", url = "https://x/h", description = "text", service = "devpost", selected = true)
        val back = Inbox.fromJson(Inbox(listOf(item, job("Data Intern", url = "https://x/j"))).toJson())
        assertEquals(2, back.items.size)
        assertEquals(item, back.items[0])
        assertEquals("Data Intern", back.items[1].title)
    }

    @Test
    fun anUnreadableSavedInboxIsAnEmptyOne() {
        assertEquals(0, Inbox.fromJson(null).items.size)
        assertEquals(0, Inbox.fromJson("").items.size)
        assertEquals(0, Inbox.fromJson("{not json").items.size)
        assertEquals(0, Inbox.fromJson("""{"items":"nope"}""").items.size)
    }

    @Test
    fun aCapturedPageIsAJobForYouToClassifyAndItsTextIsCapped() {
        val raw = JSONObject().put("kind", "page").put("title", "  A page ").put("description", "x".repeat(30_000))
        val item = InboxItem.fromCapture(raw, "mlh")
        assertEquals(JOB, item.kind)
        assertEquals("A page", item.title)
        assertEquals(20_000, item.description.length)
        assertEquals("mlh", item.service)
    }

    @Test
    fun nullFieldsReadAsEmptyNotTheWordNull() {
        val raw = JSONObject("""{"kind":"job","title":"T","company":null,"location":null}""")
        val item = InboxItem.fromCapture(raw, "linkedin")
        assertEquals("", item.company)
        assertEquals("", item.location)
    }
}
