package app.hole.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ShareParserTest {
    @Test
    fun linkedinSharesAJobWithTheCompanyInFrontOfTheTitle() {
        val items = ShareParser.parse(null, "Check out this job at Acme Analytics: Data Intern https://www.linkedin.com/jobs/view/4012345678/?trackingId=abc")
        assertEquals(1, items.size)
        val job = items.single()
        assertEquals(JOB, job.kind)
        assertEquals("Data Intern", job.title)
        assertEquals("Acme Analytics", job.company)
        assertEquals("https://www.linkedin.com/jobs/view/4012345678/?trackingId=abc", job.url)
        assertEquals(SHARE, job.service)
    }

    @Test
    fun chromeSharesThePageTitleAsTheSubjectAndOnlyTheLinkAsTheText() {
        val job = ShareParser.parse("Machine Learning Intern - Gamma Corp | LinkedIn", "https://www.linkedin.com/jobs/view/4098765432/").single()
        assertEquals("Machine Learning Intern", job.title)
        assertEquals("Gamma Corp", job.company)
        assertEquals("https://www.linkedin.com/jobs/view/4098765432/", job.url)
    }

    @Test
    fun theLineBeforeALinkIsWhatWasSaidAboutIt() {
        val job = ShareParser.parse(null, "Data Science Intern at Beta Labs - Remote\nhttps://example.com/jobs/42").single()
        assertEquals("Data Science Intern", job.title)
        assertEquals("Beta Labs", job.company)
        assertEquals("Remote", job.location)
    }

    @Test
    fun titleCompanyAndPlaceSeparatedByDashes() {
        val job = ShareParser.parse(null, "Analyst Intern - Delta Capital - Mumbai https://example.com/a").single()
        assertEquals(Triple("Analyst Intern", "Delta Capital", "Mumbai"), Triple(job.title, job.company, job.location))
    }

    @Test
    fun aCompetitionSiteOrCompetitionWordsMakeItACompetition() {
        val byHost = ShareParser.parse("Spring Build Week", "https://devpost.com/software/foo").single()
        assertEquals(COMPETITION, byHost.kind)
        assertEquals("Spring Build Week", byHost.title)
        assertEquals("", byHost.company)

        val byWords = ShareParser.parse(null, "Global AI Hackathon 2026\nhttps://example.org/event").single()
        assertEquals(COMPETITION, byWords.kind)
    }

    @Test
    fun severalLinksMakeSeveralItems() {
        val items = ShareParser.parse(null, "Global AI Hackathon https://example.org/hack\nData Intern at Acme https://example.org/job")
        assertEquals(listOf(COMPETITION, JOB), items.map { it.kind })
        assertEquals(listOf("Global AI Hackathon", "Data Intern"), items.map { it.title })
        assertEquals("Acme", items[1].company)
    }

    @Test
    fun punctuationAfterALinkIsNotPartOfIt() {
        val item = ShareParser.parse(null, "Worth a look: Data Intern at Acme (https://example.com/x).").single()
        assertEquals("https://example.com/x", item.url)
    }

    @Test
    fun textWithNoLinkStillMakesAnItemToFillIn() {
        val item = ShareParser.parse(null, "Software Engineer Intern at Acme Corp").single()
        assertEquals("", item.url)
        assertEquals("Software Engineer Intern", item.title)
        assertEquals("Acme Corp", item.company)
    }

    @Test
    fun aLinkWithNothingSaidAboutItIsTitledByItsSite() {
        assertEquals("example.com", ShareParser.parse(null, "https://example.com/x").single().title)
    }

    @Test
    fun nothingSharedMakesNothing() {
        assertTrue(ShareParser.parse(null, null).isEmpty())
        assertTrue(ShareParser.parse("  ", " \n ").isEmpty())
    }

    @Test
    fun theInboxTicksOnlyTheItemsThatAreReadyToSend() {
        val inbox = Inbox()
        // The first has no company, so a job is not ready; the second is a competition, which needs none.
        val added = inbox.add(ShareParser.parse(null, "https://example.com/a") + ShareParser.parse("Spring Hackathon", "https://devpost.com/x"))
        assertEquals(2, added)
        assertEquals(listOf(false, true), inbox.items.map { it.selected })
    }
}
