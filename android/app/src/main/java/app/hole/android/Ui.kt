package app.hole.android

import android.content.Context
import android.text.Editable
import android.text.TextWatcher
import android.view.View

fun Context.dp(value: Int): Int = (value * resources.displayMetrics.density + 0.5f).toInt()

/** A TextWatcher that only cares what the text ended up as. */
class Typed(private val onChange: (String) -> Unit) : TextWatcher {
    override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit

    override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit

    override fun afterTextChanged(s: Editable?) = onChange(s?.toString().orEmpty())
}

/** A control that is switched off looks it: these backgrounds are drawn without a disabled state. */
fun View.enable(on: Boolean) {
    isEnabled = on
    alpha = if (on) 1f else 0.4f
}
