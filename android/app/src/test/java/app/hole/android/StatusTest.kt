package app.hole.android

import java.time.Instant
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The same cases as desktop/test/pipeline.test.js: both apps must word a run the same way. */
class StatusTest {
    private val now = Instant.parse("2026-10-03T15:00:00Z")

    @Test
    fun agoReadsGmtStampsWithOrWithoutAnOffset() {
        assertEquals("just now", Status.ago("2026-10-03T14:59:30+00:00", now))
        assertEquals("30 min ago", Status.ago("2026-10-03T14:30:00+00:00", now))
        assertEquals("3 h ago", Status.ago("2026-10-03T12:00:00", now))
        assertEquals("2 days ago", Status.ago("2026-10-01T15:00:00+00:00", now))
        assertEquals("", Status.ago("not a date", now))
        assertEquals("", Status.ago(null, now))
    }

    @Test
    fun aStampFromTheDatabaseWithASpaceInPlaceOfTheTIsRead() {
        assertEquals("30 min ago", Status.ago("2026-10-03 14:30:00", now))
    }

    @Test
    fun aStampInTheFutureIsJustNow() {
        assertEquals("just now", Status.ago("2026-10-03T16:00:00+00:00", now))
    }

    @Test
    fun theLineSaysWhatRanWhenNextAndWhatIsLeft() {
        val idle = Status.describe(
            JSONObject(
                """{"running":false,
                    "run":{"started_at":"2026-10-03T12:00:05+00:00","finished_at":"2026-10-03T12:09:00+00:00","sourced":41,"tailored":8,"sent":0},
                    "run_at":"12:00","timezone":"GMT","auto_apply":false,
                    "counts":{"remaining":42,"applied":3}}""",
            ),
            now,
        )
        assertFalse(idle.running)
        assertEquals("Last run 3 h ago: 41 new, 8 drafted, 0 sent · next 12:00 GMT · 42 remaining, 3 applied", idle.text)
    }

    @Test
    fun aRunInProgressIsSaidSoAndAutoApplyIsFlagged() {
        val going = Status.describe(
            JSONObject(
                """{"running":true,"run":{"started_at":"2026-10-03T14:50:00+00:00"},
                    "run_at":"12:00","auto_apply":true,"counts":{"remaining":5,"applied":0}}""",
            ),
            now,
        )
        assertTrue(going.running)
        assertEquals("Running now, started 10 min ago · next 12:00 GMT · 5 remaining, 0 applied · auto-apply on", going.text)
    }

    @Test
    fun noRunYetAndNoStatusAtAll() {
        assertEquals("Not run yet · next 12:00 GMT", Status.describe(JSONObject("""{"running":false,"run":null,"run_at":"12:00"}"""), now).text)
        assertEquals("Not run yet", Status.describe(null, now).text)
    }

    @Test
    fun aRunStillWithoutItsCountsReadsAsZeroNotNull() {
        val line = Status.describe(JSONObject("""{"running":false,"run":{"started_at":"2026-10-03T14:30:00+00:00","sourced":null}}"""), now)
        assertEquals("Last run 30 min ago: 0 new, 0 drafted, 0 sent", line.text)
    }
}
