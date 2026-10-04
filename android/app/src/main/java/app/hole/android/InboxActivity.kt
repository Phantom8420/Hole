package app.hole.android

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * What was captured or shared, for you to look at before anything leaves the phone. As on the
 * desktop: edit what is wrong, tick what you want, and "Send selected to Hole" posts those, and
 * only those, to the server's /api/ingest.
 */
class InboxActivity : Activity() {
    private lateinit var inbox: Inbox
    private lateinit var list: LinearLayout
    private lateinit var status: TextView
    private lateinit var send: Button

    private val io: ExecutorService = Executors.newSingleThreadExecutor()
    private val ui = Handler(Looper.getMainLooper())
    private var destroyed = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_inbox)
        inbox = InboxStore.get(this)
        list = findViewById(R.id.items)
        status = findViewById(R.id.status)
        send = findViewById(R.id.send)
        findViewById<View>(R.id.done).setOnClickListener { finish() }
        findViewById<View>(R.id.clear).setOnClickListener {
            inbox.clear()
            render()
            setStatus("", R.color.hole_muted)
        }
        send.setOnClickListener { sendSelected() }
        render()
        intent.getStringExtra(EXTRA_NOTE)?.let { setStatus(it, R.color.hole_good) }
    }

    /** Something was shared while this screen was already open. */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        render()
        intent.getStringExtra(EXTRA_NOTE)?.let { setStatus(it, R.color.hole_good) }
    }

    override fun onPause() {
        InboxStore.save(this)
        super.onPause()
    }

    override fun onDestroy() {
        destroyed = true
        io.shutdown()
        super.onDestroy()
    }

    private fun setStatus(message: String, colour: Int) {
        status.text = message
        status.setTextColor(getColor(colour))
    }

    private fun render() {
        list.removeAllViews()
        if (inbox.items.isEmpty()) {
            val none = TextView(this)
            none.text = "Nothing captured yet. Open a service tab and press Capture, or share a job to Hole from another app."
            none.setTextColor(getColor(R.color.hole_muted))
            none.textSize = 14f
            none.setPadding(dp(16), dp(16), dp(16), dp(16))
            list.addView(none)
            return
        }
        for (item in inbox.items) {
            list.addView(row(item))
            val line = View(this)
            line.setBackgroundColor(getColor(R.color.hole_line))
            list.addView(line, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(1)))
        }
    }

    private fun row(item: InboxItem): View {
        val box = LinearLayout(this)
        box.orientation = LinearLayout.VERTICAL
        box.setPadding(dp(12), dp(10), dp(12), dp(10))

        val top = LinearLayout(this)
        top.gravity = Gravity.CENTER_VERTICAL
        val tick = CheckBox(this)
        tick.isChecked = item.selected
        tick.setOnCheckedChangeListener { _, on -> item.selected = on }
        top.addView(tick)
        top.addView(field(item.title, "Title") { item.title = it }, LinearLayout.LayoutParams(0, dp(42), 1f))
        val kind = Button(this, null, 0, R.style.HoleButton)
        kind.text = if (item.kind == JOB) "Job" else "Competition"
        kind.setOnClickListener {
            item.kind = if (item.kind == JOB) COMPETITION else JOB
            render()
        }
        val kindParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, dp(36))
        kindParams.marginStart = dp(8)
        top.addView(kind, kindParams)
        box.addView(top)

        val details = LinearLayout(this)
        details.setPadding(0, dp(6), 0, dp(4))
        if (item.kind == JOB) {
            details.addView(field(item.company, "Company") { item.company = it }, LinearLayout.LayoutParams(0, dp(42), 1f))
            val place = LinearLayout.LayoutParams(0, dp(42), 1f)
            place.marginStart = dp(8)
            details.addView(field(item.location, "Location") { item.location = it }, place)
        } else {
            details.addView(field(item.deadline, "Deadline") { item.deadline = it }, LinearLayout.LayoutParams(0, dp(42), 1f))
        }
        box.addView(details)

        val origin = TextView(this)
        origin.text = "${item.service} · ${item.url}"
        origin.setTextColor(getColor(R.color.hole_muted))
        origin.textSize = 11f
        origin.maxLines = 1
        origin.ellipsize = TextUtils.TruncateAt.END
        box.addView(origin)
        return box
    }

    private fun field(value: String, hint: String, onChange: (String) -> Unit): EditText {
        val input = EditText(this, null, 0, R.style.HoleInput)
        input.setText(value)
        input.hint = hint
        input.inputType = InputType.TYPE_CLASS_TEXT
        input.importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_NO
        // After setText, so filling the field in does not count as an edit.
        input.addTextChangedListener(Typed(onChange))
        return input
    }

    private fun sendSelected() {
        val chosen = inbox.items.filter { it.selected }
        if (chosen.isEmpty()) return setStatus("Tick at least one item first", R.color.hole_bad)
        if (!chosen.all { it.complete }) return setStatus("Jobs need a title and a company", R.color.hole_bad)
        val prefs = Prefs(this)
        val token = prefs.token
        if (token.isEmpty()) return setStatus("Set the API token first: open Hole and tap the gear.", R.color.hole_bad)

        val api = HoleApi(prefs.holeUrl, token)
        send.enable(false)
        setStatus("Sending…", R.color.hole_muted)
        io.execute {
            var total = HoleApi.Ingested()
            val sent = ArrayList<InboxItem>()
            var failure: String? = null
            try {
                // One request per source, as the desktop app does: the server records where each came from.
                for ((service, group) in chosen.groupBy { it.service }) {
                    total += api.ingest(service, group)
                    sent += group
                }
            } catch (e: Exception) {
                failure = e.message ?: "Could not send"
            }
            ui.post {
                if (destroyed) return@post
                send.enable(true)
                // What did go is not offered again; what did not stays, so nothing is lost to a bad connection.
                inbox.removeAll(sent)
                InboxStore.save(this)
                render()
                if (failure != null) {
                    setStatus(failure, R.color.hole_bad)
                } else {
                    val rejected = if (total.rejected > 0) ", ${total.rejected} rejected" else ""
                    setStatus("Sent: ${total.new} new, ${total.known} already in Hole$rejected", R.color.hole_good)
                }
            }
        }
    }

    companion object {
        private const val EXTRA_NOTE = "note"

        fun open(context: Context, note: String? = null): Intent =
            Intent(context, InboxActivity::class.java).putExtra(EXTRA_NOTE, note)
    }
}
