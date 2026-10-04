package app.hole.android

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.widget.Toast

/**
 * The "Send to Hole" entry in another app's share sheet. It has no screen of its own: it reads the
 * text and link the app handed over, puts them in the inbox for review, and opens the inbox. Nothing
 * is fetched and nothing is sent from here.
 */
class ShareActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val subject = intent.getCharSequenceExtra(Intent.EXTRA_SUBJECT)?.toString()
        val text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString()
        val found = ShareParser.parse(subject, text)
        if (found.isEmpty()) {
            Toast.makeText(this, "There was nothing in that to add to Hole", Toast.LENGTH_SHORT).show()
        } else {
            val added = InboxStore.get(this).add(found)
            InboxStore.save(this)
            val note = if (added == 0) "Already in the inbox" else "Added $added to the inbox. Check them, then send."
            startActivity(InboxActivity.open(this, note))
        }
        finish()
    }
}
