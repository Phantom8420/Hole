package app.hole.android

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CaptureTest {
    // What WebView.evaluateJavascript calls back with: the script's string result, as a JSON string.
    private fun raw(scriptResult: String) = JSONObject.quote(scriptResult)

    @Test
    fun theItemsTheExtractorsFoundBecomeInboxItems() {
        val result = Capture.parse(
            raw("""[{"kind":"job","title":"Data Intern","company":"Acme","location":"Pune","url":"https://x/1"},
                    {"kind":"competition","title":"Spring Datathon","url":"https://x/2","deadline":"2026-11-15"},
                    {"kind":"page","title":"Some page","url":"https://x/3"},
                    {"kind":"job","title":"","url":"https://x/4"}]"""),
            "linkedin",
        )
        val items = (result as Capture.Found).items
        assertEquals(listOf("Data Intern", "Spring Datathon", "Some page"), items.map { it.title })
        assertEquals(listOf(JOB, COMPETITION, JOB), items.map { it.kind })
        assertEquals("2026-11-15", items[1].deadline)
        assertTrue(items.all { it.service == "linkedin" })
    }

    @Test
    fun anEmptyResultIsFoundNothingNotAFailure() {
        assertEquals(Capture.Found(emptyList()), Capture.parse(raw("[]"), "mlh"))
    }

    @Test
    fun aScriptThatThrewReportsWhy() {
        val result = Capture.parse(raw("""{"error":"x is not defined"}"""), "mlh")
        assertEquals(Capture.Failed("x is not defined"), result)
    }

    @Test
    fun nothingBackFromThePageIsAFailure() {
        assertTrue(Capture.parse(null, "mlh") is Capture.Failed)
        assertTrue(Capture.parse("null", "mlh") is Capture.Failed)
        assertTrue(Capture.parse("42", "mlh") is Capture.Failed)
        assertTrue(Capture.parse(raw("not json"), "mlh") is Capture.Failed)
    }

    @Test
    fun theScriptCarriesTheLibraryAndAskForTheServicesExtractors() {
        val script = Capture.script("function holeExtract(names) { return []; }", listOf("linkedin", "jsonld"))
        assertTrue(script.startsWith("(function(){try{function holeExtract"))
        assertTrue(script.contains("""JSON.stringify(holeExtract(["linkedin","jsonld"]))"""))
        assertTrue(script.endsWith("})()"))
    }

    @Test
    fun theLibraryTheAppShipsDefinesWhatTheScriptCalls() {
        val library = javaClass.classLoader!!.getResourceAsStream("extractors.js")!!.readBytes().toString(Charsets.UTF_8)
        assertTrue(library.contains("function holeExtract("))
        for (name in listOf("jsonld", "linkedin", "indeed", "links", "page")) assertTrue("no $name extractor", library.contains("function $name("))
    }
}
