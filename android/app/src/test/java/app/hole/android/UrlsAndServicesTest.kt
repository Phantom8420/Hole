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
    private val catalog = Services.parse(javaClass.classLoader!!.getResourceAsStream("services.json")!!.readBytes().toString(Charsets.UTF_8))
    private val services = catalog.services

    @Test
    fun theAppHasTheSameServicesInTheSameOrderAsTheDesktop() {
        assertEquals(listOf("hole", "linkedin", "indeed", "discord", "proofr", "unstop", "devfolio", "devpost", "mlh"), services.map { it.id })
    }

    @Test
    fun theRailIsThreePlacesWithTheSitesInEachInTheDesktopsOrder() {
        assertEquals(listOf("dashboard", "listings", "social"), catalog.sections.map { it.id })
        assertEquals(listOf("Dashboard", "Listings", "Social"), catalog.sections.map { it.name })
        assertEquals(listOf("hole", "listings", "social"), catalog.sections.map { it.icon })
        val sites = catalog.sections.associate { place -> place.id to catalog.sitesIn(place).map { it.id } }
        assertEquals(listOf("hole"), sites["dashboard"])
        assertEquals(listOf("indeed", "proofr", "unstop", "devfolio", "devpost", "mlh"), sites["listings"])
        assertEquals(listOf("linkedin", "discord"), sites["social"])
        // every service is in exactly one place
        assertEquals(services.map { it.id }.sorted(), sites.values.flatten().sorted())
    }

    // A catalog built from a few rows, to test what parse does with a service that names no place.
    private fun parse(vararg rows: String) = Services.parse(
        "{\"sections\":[{\"id\":\"dashboard\",\"name\":\"Dashboard\",\"icon\":\"hole\"}," +
            "{\"id\":\"listings\",\"name\":\"Listings\",\"icon\":\"listings\"}," +
            "{\"id\":\"social\",\"name\":\"Social\",\"icon\":\"social\"}],\"services\":[${rows.joinToString(",")}]}",
    )

    private fun row(id: String, section: String? = null, level: String = "capture"): String {
        val place = if (section == null) "" else "\"section\":\"$section\","
        return "{\"id\":\"$id\",\"name\":\"$id\",\"glyph\":\"\",\"url\":null,\"level\":\"$level\",${place}\"extractors\":[]}"
    }

    @Test
    fun aServiceWithNoPlaceOrAnUnknownOneIsInListingsAndHoleIsAlwaysTheDashboard() {
        val parsed = parse(row("hole", "social"), row("a"), row("b", "nowhere"), row("c", "social"), row("d", "dashboard"))
        val place = parsed.services.associate { it.id to it.section }
        assertEquals(mapOf("hole" to "dashboard", "a" to "listings", "b" to "listings", "c" to "social", "d" to "listings"), place)
    }

    @Test
    fun aPlaceWithNothingInItIsLeftOffTheRail() {
        assertEquals(listOf("dashboard", "listings"), parse(row("hole"), row("a")).sections.map { it.id })
    }

    @Test
    fun aPlaceTakesTheHighestLevelOfItsSites() {
        val parsed = parse(row("hole", level = "view"), row("a", "listings"), row("b", "listings", "batch"), row("c", "social", "view"))
        fun level(id: String) = parsed.levelOf(parsed.sections.first { it.id == id })
        assertEquals(Level.VIEW, level("dashboard"))
        assertEquals(Level.BATCH, level("listings"))
        assertEquals(Level.VIEW, level("social"))
        assertEquals(Level.CAPTURE, catalog.levelOf(catalog.sections.first { it.id == "social" }))
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
