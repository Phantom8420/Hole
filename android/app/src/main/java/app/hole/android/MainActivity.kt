package app.hole.android

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.res.ColorStateList
import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.JsResult
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * Hole on a phone: the same shell as the desktop app. The service rail runs along the bottom, the
 * page of the service you picked fills the middle (Hole's own pages are the dashboard, signed in
 * with the same web password), and the toolbar and pipeline line are the desktop's, folded to fit.
 *
 * The pipeline runs on the server, never here: the 12:00 GMT run happens whether or not this app
 * is open, and "Update listings" only asks the server to start one now.
 */
class MainActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var services: List<Service>
    private lateinit var library: String

    private lateinit var stage: FrameLayout
    private lateinit var emptyBox: View
    private lateinit var emptyMessage: TextView
    private lateinit var emptyAddress: EditText
    private lateinit var rail: LinearLayout
    private lateinit var railScroll: HorizontalScrollView
    private lateinit var settingsSlot: FrameLayout
    private lateinit var backButton: View
    private lateinit var serviceName: TextView
    private lateinit var serviceLevel: TextView
    private lateinit var captureButton: Button
    private lateinit var inboxButton: Button
    private lateinit var pipelineText: TextView
    private lateinit var updateButton: Button

    private val webViews = HashMap<String, WebView>()
    private val failedPages = HashMap<String, String>()
    private var active: Service? = null
    private var resumed = false
    private var destroyed = false

    private val io: ExecutorService = Executors.newSingleThreadExecutor()
    private val ui = Handler(Looper.getMainLooper())
    private val pollStatus = Runnable { refreshPipeline() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        prefs = Prefs(this)
        services = Services.parse(asset("services.json"))
        library = asset("extractors.js")
        // The debug build can be inspected from chrome://inspect on a computer; a release build cannot.
        if ((applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0) WebView.setWebContentsDebuggingEnabled(true)

        stage = findViewById(R.id.stage)
        emptyBox = findViewById(R.id.empty)
        emptyMessage = findViewById(R.id.empty_message)
        emptyAddress = findViewById(R.id.empty_address)
        rail = findViewById(R.id.rail)
        railScroll = findViewById(R.id.rail_scroll)
        settingsSlot = findViewById(R.id.settings_slot)
        backButton = findViewById(R.id.back)
        serviceName = findViewById(R.id.service_name)
        serviceLevel = findViewById(R.id.service_level)
        captureButton = findViewById(R.id.capture)
        inboxButton = findViewById(R.id.inbox)
        pipelineText = findViewById(R.id.pipeline)
        updateButton = findViewById(R.id.update)

        backButton.setOnClickListener { activeView()?.let { if (it.canGoBack()) it.goBack() } }
        findViewById<View>(R.id.reload).setOnClickListener { reload() }
        captureButton.setOnClickListener { capturePage() }
        inboxButton.setOnClickListener { startActivity(Intent(this, InboxActivity::class.java)) }
        updateButton.setOnClickListener { startRun() }
        findViewById<View>(R.id.empty_open).setOnClickListener { openTypedAddress() }

        val remembered = savedInstanceState?.getString(STATE_ACTIVE)
        show(services.firstOrNull { it.id == remembered } ?: services.first())
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString(STATE_ACTIVE, active?.id)
    }

    override fun onResume() {
        super.onResume()
        resumed = true
        webViews.values.forEach { it.onResume() }
        updateInboxBadge()
        refreshPipeline()
    }

    override fun onPause() {
        resumed = false
        ui.removeCallbacks(pollStatus)
        webViews.values.forEach { it.onPause() }
        CookieManager.getInstance().flush()
        super.onPause()
    }

    override fun onDestroy() {
        destroyed = true
        ui.removeCallbacksAndMessages(null)
        for (view in webViews.values) {
            (view.parent as? ViewGroup)?.removeView(view)
            view.destroy()
        }
        webViews.clear()
        io.shutdown()
        super.onDestroy()
    }

    @Suppress("DEPRECATION", "OVERRIDE_DEPRECATION")
    override fun onBackPressed() {
        val view = activeView()
        when {
            view != null && view.canGoBack() -> view.goBack()
            active?.isHole == false -> show(services.first { it.isHole })
            else -> super.onBackPressed()
        }
    }

    // ------------------------------------------------------------------ rail and stage

    private fun addressOf(service: Service): String? =
        if (service.isHole) prefs.holeUrl else prefs.serviceUrl(service.id) ?: service.url

    private fun activeView(): WebView? = active?.let { webViews[it.id] }

    private fun show(service: Service) {
        active = service
        val address = addressOf(service)
        if (address != null && service.id !in webViews) {
            val view = newWebView(service)
            webViews[service.id] = view
            stage.addView(view, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
            view.loadUrl(address)
        }
        for ((id, view) in webViews) view.visibility = if (id == service.id) View.VISIBLE else View.GONE
        emptyBox.visibility = if (address == null) View.VISIBLE else View.GONE
        if (address == null) {
            emptyMessage.text = "${service.name} has no address yet."
            emptyAddress.hint = "Address for ${service.name}"
            emptyAddress.setText("")
        }
        renderRail()
        renderToolbar()
    }

    private fun renderRail() {
        rail.removeAllViews()
        for (service in services) {
            val params = LinearLayout.LayoutParams(dp(TILE), dp(TILE))
            params.marginEnd = dp(8)
            rail.addView(tile(service, active?.id == service.id) { show(service) }, params)
        }
        settingsSlot.removeAllViews()
        settingsSlot.addView(tile(null, false) { openSettings() }, FrameLayout.LayoutParams(dp(TILE), dp(TILE)))
        val index = services.indexOfFirst { it.id == active?.id }
        railScroll.post { rail.getChildAt(index)?.let { railScroll.smoothScrollTo(maxOf(0, it.left - dp(16)), 0) } }
    }

    /** One button on the rail: the service's mark, with a dot for how much the app may do there. A null service is Settings. */
    private fun tile(service: Service?, selected: Boolean, onClick: () -> Unit): View {
        val frame = FrameLayout(this)
        frame.setBackgroundResource(if (selected) R.drawable.bg_button_primary else R.drawable.bg_button)
        frame.contentDescription = service?.let { "${it.name}, ${it.level.label}" } ?: getString(R.string.settings)
        frame.isClickable = true
        frame.isFocusable = true
        frame.setOnClickListener { onClick() }

        val ink = getColor(if (selected) R.color.hole_ink else R.color.hole_text)
        val mark = markFor(service?.id ?: "settings")
        if (mark != null) {
            val image = ImageView(this)
            image.setImageDrawable(mark)
            image.imageTintList = ColorStateList.valueOf(ink)
            frame.addView(image, FrameLayout.LayoutParams(dp(22), dp(22), Gravity.CENTER))
        } else {
            // A service with no mark of its own shows the letters of its glyph, as the desktop rail does.
            val letters = TextView(this)
            letters.text = service?.glyph ?: "⚙"
            letters.gravity = Gravity.CENTER
            letters.textSize = 12f
            letters.setTypeface(letters.typeface, Typeface.BOLD)
            letters.setTextColor(ink)
            frame.addView(letters, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        }
        if (service != null) {
            val dot = View(this)
            dot.setBackgroundResource(R.drawable.bg_dot)
            val colour = when (service.level) {
                Level.VIEW -> R.color.hole_muted
                Level.CAPTURE -> R.color.hole_good
                Level.BATCH -> R.color.hole_accent
            }
            dot.backgroundTintList = ColorStateList.valueOf(getColor(colour))
            val params = FrameLayout.LayoutParams(dp(6), dp(6), Gravity.TOP or Gravity.END)
            params.topMargin = dp(5)
            params.marginEnd = dp(5)
            frame.addView(dot, params)
        }
        return frame
    }

    @SuppressLint("DiscouragedApi")
    private fun markFor(id: String): Drawable? {
        val resource = resources.getIdentifier("ic_$id", "drawable", packageName)
        return if (resource == 0) null else getDrawable(resource)?.mutate()
    }

    private fun renderToolbar() {
        val service = active ?: return
        serviceName.text = service.name
        serviceLevel.text = service.level.label
        captureButton.visibility = if (service.level == Level.VIEW) View.GONE else View.VISIBLE
        updateNav()
        updateInboxBadge()
    }

    private fun updateNav() {
        val view = activeView()
        backButton.enable(view?.canGoBack() == true)
        captureButton.enable(view != null)
    }

    private fun updateInboxBadge() {
        inboxButton.text = "Inbox ${InboxStore.get(this).items.size}"
    }

    private fun reload() {
        val service = active ?: return
        val view = webViews[service.id] ?: return
        val retry = failedPages[service.id]
        if (retry != null) view.loadUrl(retry) else view.reload()
    }

    private fun openTypedAddress() {
        val service = active ?: return
        val address = Urls.web(emptyAddress.text.toString())
        if (address == null) {
            toast("That is not a web address")
            return
        }
        prefs.setServiceUrl(service.id, address)
        show(service)
    }

    // ------------------------------------------------------------------ pages

    @SuppressLint("SetJavaScriptEnabled")
    private fun newWebView(service: Service): WebView {
        val view = WebView(this)
        view.setBackgroundColor(getColor(R.color.hole_bg))
        with(view.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            // Nothing a page loads may read this app's files or content providers, or fall back to http.
            allowFileAccess = false
            allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            setSupportMultipleWindows(false)
            mediaPlaybackRequiresUserGesture = true
            setGeolocationEnabled(false)
            safeBrowsingEnabled = true
        }
        // Hole's own pages need no cookies from anyone else; a service's sign-in often does.
        CookieManager.getInstance().setAcceptThirdPartyCookies(view, !service.isHole)
        view.webViewClient = pageClient(service)
        view.webChromeClient = dialogs()
        return view
    }

    private fun pageClient(service: Service) = object : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val uri = request.url
            val web = uri.scheme.equals("http", ignoreCase = true) || uri.scheme.equals("https", ignoreCase = true)
            // A service's own pages load here; anything that is not a web page (intent:, market:) is dropped.
            if (!service.isHole) return !web
            // Hole's pages stay in Hole; a link out to a posting opens in the browser, signed in as you are there.
            if (web && Urls.sameHost(uri.toString(), prefs.holeUrl)) return false
            openOutside(uri)
            return true
        }

        override fun onPageFinished(view: WebView, url: String?) {
            if (url != null && url != "about:blank") failedPages.remove(service.id)
            if (active?.id == service.id) updateNav()
            CookieManager.getInstance().flush()
        }

        override fun doUpdateVisitedHistory(view: WebView, url: String?, isReload: Boolean) {
            if (active?.id == service.id) updateNav()
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (!request.isForMainFrame) return
            failedPages[service.id] = request.url.toString()
            view.loadDataWithBaseURL(null, errorPage(service.name, request.url, error.description?.toString()), "text/html", "utf-8", null)
        }
    }

    /** alert() and confirm(), which a WebView drops silently unless the app shows them. */
    private fun dialogs() = object : WebChromeClient() {
        override fun onJsAlert(view: WebView, url: String?, message: String?, result: JsResult): Boolean {
            AlertDialog.Builder(this@MainActivity, R.style.Theme_Hole_Dialog)
                .setMessage(message)
                .setPositiveButton(android.R.string.ok) { _, _ -> result.confirm() }
                .setOnCancelListener { result.cancel() }
                .show()
            return true
        }

        override fun onJsConfirm(view: WebView, url: String?, message: String?, result: JsResult): Boolean {
            AlertDialog.Builder(this@MainActivity, R.style.Theme_Hole_Dialog)
                .setMessage(message)
                .setPositiveButton(android.R.string.ok) { _, _ -> result.confirm() }
                .setNegativeButton(android.R.string.cancel) { _, _ -> result.cancel() }
                .setOnCancelListener { result.cancel() }
                .show()
            return true
        }
    }

    private fun errorPage(name: String, address: Uri, reason: String?): String {
        val where = TextUtils.htmlEncode(address.host ?: address.toString())
        val why = TextUtils.htmlEncode(reason.orEmpty())
        return "<html><head><meta name='viewport' content='width=device-width, initial-scale=1'></head>" +
            "<body style='background:#14110F;color:#F2EBE4;font-family:sans-serif;padding:32px'>" +
            "<h3>Cannot open ${TextUtils.htmlEncode(name)}</h3>" +
            "<p style='color:#8D8178'>$where: $why</p>" +
            "<p style='color:#8D8178'>Check the connection, then tap the reload button.</p></body></html>"
    }

    private fun openOutside(uri: Uri) {
        if (uri.scheme?.lowercase() !in OPENABLE) return
        try {
            startActivity(Intent(Intent.ACTION_VIEW, uri))
        } catch (_: ActivityNotFoundException) {
            toast("Nothing here can open that link")
        }
    }

    // ------------------------------------------------------------------ the pipeline

    private fun showPipeline(text: String, colour: Int) {
        pipelineText.text = text
        pipelineText.setTextColor(getColor(colour))
    }

    /** Ask the server how the pipeline stands; poll quickly while it runs and slowly otherwise. */
    private fun refreshPipeline() {
        ui.removeCallbacks(pollStatus)
        val token = prefs.token
        if (token.isEmpty()) {
            showPipeline("Add the API token in Settings (the gear) to update listings from here.", R.color.hole_muted)
            return
        }
        val api = HoleApi(prefs.holeUrl, token)
        io.execute {
            var running = false
            var colour = R.color.hole_muted
            val line = try {
                val described = Status.describe(api.status())
                running = described.running
                if (running) colour = R.color.hole_accent
                described.text
            } catch (e: Exception) {
                colour = R.color.hole_bad
                e.message ?: "Could not reach the server"
            }
            ui.post {
                if (destroyed) return@post
                showPipeline(line, colour)
                if (resumed) ui.postDelayed(pollStatus, if (running) 5_000L else 30_000L)
            }
        }
    }

    /** "Update listings": have the server fetch, filter and prepare applications now. Asking twice is harmless. */
    private fun startRun() {
        val token = prefs.token
        if (token.isEmpty()) {
            openSettings()
            return
        }
        updateButton.enable(false)
        val api = HoleApi(prefs.holeUrl, token)
        io.execute {
            var colour = R.color.hole_accent
            val message = try {
                if (api.startRun().started) "Started on the server; this takes a few minutes" else "A run is already going"
            } catch (e: Exception) {
                colour = R.color.hole_bad
                e.message ?: "Could not start a run"
            }
            ui.post {
                if (destroyed) return@post
                updateButton.enable(true)
                showPipeline(message, colour)
                ui.removeCallbacks(pollStatus)
                ui.postDelayed(pollStatus, 4_000L)
            }
        }
    }

    // ------------------------------------------------------------------ capture

    /** Read the page you are looking at (when you press the button, and only then) into the inbox. */
    private fun capturePage() {
        val service = active ?: return
        val view = webViews[service.id] ?: return
        if (service.level == Level.VIEW) return
        captureButton.enable(false)
        toast("Reading the page…")
        view.evaluateJavascript(Capture.script(library, service.extractors)) { raw ->
            if (destroyed) return@evaluateJavascript
            captureButton.enable(true)
            when (val result = Capture.parse(raw, service.id)) {
                is Capture.Failed -> toast(result.message)
                is Capture.Found -> {
                    val added = InboxStore.get(this).add(result.items)
                    InboxStore.save(this)
                    updateInboxBadge()
                    if (result.items.isEmpty()) {
                        toast("Nothing recognised on this page")
                    } else {
                        startActivity(InboxActivity.open(this, "Found ${result.items.size}, $added new to this list"))
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------------ settings

    private fun openSettings() {
        val form = layoutInflater.inflate(R.layout.dialog_settings, null)
        val address = form.findViewById<EditText>(R.id.hole_url)
        val token = form.findViewById<EditText>(R.id.token)
        val error = form.findViewById<TextView>(R.id.settings_error)
        address.setText(prefs.holeUrl)
        token.hint = if (prefs.token.isNotEmpty()) "Saved - type to replace" else "Paste the token"

        val dialog = AlertDialog.Builder(this, R.style.Theme_Hole_Dialog)
            .setTitle(R.string.settings)
            .setView(form)
            .setPositiveButton(R.string.save, null)
            .setNegativeButton(R.string.cancel, null)
            .create()
        // Set after show() so a bad value keeps the dialog open instead of closing it.
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val origin = Urls.holeOrigin(address.text.toString())
                if (origin == null) {
                    error.text = "The address must start with https://"
                    return@setOnClickListener
                }
                val pasted = token.text.toString().trim()
                if (pasted.isNotEmpty()) {
                    try {
                        prefs.token = pasted
                    } catch (e: Exception) {
                        error.text = "This phone could not store the token securely, so it was not saved"
                        return@setOnClickListener
                    }
                }
                val moved = origin != prefs.holeUrl
                prefs.holeUrl = origin
                // Hole's page keeps the address it was opened with; take it to the new one.
                if (moved) webViews[HOLE_ID]?.loadUrl(origin)
                dialog.dismiss()
                refreshPipeline()
            }
        }
        dialog.show()
    }

    private fun asset(name: String): String = assets.open(name).bufferedReader(Charsets.UTF_8).use { it.readText() }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    private companion object {
        const val STATE_ACTIVE = "active"
        const val TILE = 44
        val OPENABLE = setOf("http", "https", "mailto", "tel")
    }
}
