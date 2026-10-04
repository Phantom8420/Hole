package app.hole.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class UrlsAndServicesTest {
    @Test
    fun theServerAddressIsReducedToItsOriginAndMustBeHttps() {
        assertEquals("https://hole-roan.vercel.app", Urls.holeOrigin("https://hole-roan.vercel.app/some/page?x=1"))
        assertEquals("https://hole-roan.vercel.app", Urls.holeOrigin("  Hole-Roan.Vercel.app "))
        assertEquals("https://80-225-233-74.sslip.io", Urls.holeOrigin("https://80-225-233-74.sslip.io:443/"))
        assertEquals("https://example.com:8443", Urls.holeOrigin("https://example.com:8443/x"))
        assertNull(Urls.holeOrigin("http://hole.example"))
        assertNull(Urls.holeOrigin("ftp://hole.example"))
        assertNull(Urls.holeOrigin(""))
        assertNull(Urls.holeOrigin("https://"))
        assertNull(Urls.holeOrigin("not a url at all"))
    }

    @Test
    fun aServiceAddressIsAnyWebPage() {
        assertEquals("https://proofr.example/jobs", Urls.web("https://proofr.example/jobs"))
        assertEquals("https://proofr.example", Urls.web("proofr.example"))
        assertEquals("http://localhost:3000/", Urls.web("http://localhost:3000/"))
        assertNull(Urls.web("javascript:alert(1)"))
        assertNull(Urls.web("file:///etc/passwd"))
        assertNull(Urls.web(""))
    }

    @Test
    fun sameHostComparesHostsOnly() {
        assertTrue(Urls.sameHost("https://hole-roan.vercel.app/a", "https://HOLE-ROAN.vercel.app"))
        assertFalse(Urls.sameHost("https://evil.example/hole-roan.vercel.app", "https://hole-roan.vercel.app"))
        assertFalse(Urls.sameHost("https://hole-roan.vercel.app.evil.example", "https://hole-roan.vercel.app"))
        assertFalse(Urls.sameHost(null, "https://hole-roan.vercel.app"))
    }

    // services.json is generated from the desktop app's list (android/tools/sync.js) and shipped as an asset.
    private val services = Services.parse(javaClass.classLoader!!.getResourceAsStream("services.json")!!.readBytes().toString(Charsets.UTF_8))

    @Test
    fun theRailHasTheSameServicesInTheSameOrderAsTheDesktop() {
        assertEquals(listOf("hole", "linkedin", "indeed", "discord", "proofr", "unstop", "devfolio", "devpost", "mlh"), services.map { it.id })
    }

    @Test
    fun holeIsViewOnlyWithItsAddressFromSettingsAndEveryOtherServiceCapturesOnRequest() {
        val hole = services.first()
        assertTrue(hole.isHole)
        assertNull(hole.url)
        assertEquals(Level.VIEW, hole.level)
        assertTrue(services.drop(1).all { it.level == Level.CAPTURE })
        assertTrue(services.drop(1).all { it.extractors.isNotEmpty() })
    }

    @Test
    fun everyAddressIsHttpsAndProofrHasNoneUntilYouGiveIt() {
        assertNull(services.first { it.id == "proofr" }.url)
        assertTrue(services.filter { !it.isHole && it.id != "proofr" }.all { it.url!!.startsWith("https://") })
    }
}
