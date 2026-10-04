package app.hole.android

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * What the app keeps between runs: the server's address, any address you gave a service that has
 * none, and the API token. The token is the only secret. It is sealed with an AES key that lives in
 * the Android Keystore and cannot be read out of it, so what is written to disk is useless without
 * this app on this phone. The web password is never stored here: you type it into the Hole page,
 * and the page's own session cookie does the rest.
 */
class Prefs(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("hole", Context.MODE_PRIVATE)

    var holeUrl: String
        get() = prefs.getString(KEY_URL, null) ?: Urls.DEFAULT_HOLE
        set(value) = prefs.edit().putString(KEY_URL, value).apply()

    /** Empty when none is saved, or when the Keystore can no longer open it (after a lock-screen reset, say). */
    var token: String
        get() = prefs.getString(KEY_TOKEN, null)?.let { Vault.open(it) }.orEmpty()
        set(value) {
            val edit = prefs.edit()
            if (value.isEmpty()) edit.remove(KEY_TOKEN) else edit.putString(KEY_TOKEN, Vault.seal(value))
            edit.apply()
        }

    fun serviceUrl(id: String): String? = prefs.getString("$KEY_SERVICE$id", null)

    fun setServiceUrl(id: String, url: String) = prefs.edit().putString("$KEY_SERVICE$id", url).apply()

    private companion object {
        const val KEY_URL = "hole_url"
        const val KEY_TOKEN = "token"
        const val KEY_SERVICE = "service_url_"
    }
}

private object Vault {
    private const val PROVIDER = "AndroidKeyStore"
    private const val ALIAS = "hole.token"
    private const val TRANSFORMATION = "AES/GCM/NoPadding"
    private const val TAG_BITS = 128
    private const val IV_BYTES = 12

    private fun key(): SecretKey {
        val store = KeyStore.getInstance(PROVIDER).apply { load(null) }
        (store.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, PROVIDER)
        generator.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return generator.generateKey()
    }

    /** The IV the Keystore picked, then the ciphertext, as one Base64 string. */
    fun seal(plain: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val sealed = cipher.iv + cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(sealed, Base64.NO_WRAP)
    }

    fun open(sealed: String): String? = try {
        val raw = Base64.decode(sealed, Base64.NO_WRAP)
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, raw, 0, IV_BYTES))
        String(cipher.doFinal(raw, IV_BYTES, raw.size - IV_BYTES), Charsets.UTF_8)
    } catch (_: Exception) {
        // A key the Keystore no longer holds, a corrupt value, a device whose Keystore misbehaves:
        // all mean the token has to be pasted again, none is worth a crash.
        null
    }
}
