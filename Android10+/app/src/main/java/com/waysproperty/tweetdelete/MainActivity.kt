package com.waysproperty.tweetdelete

import android.app.Activity
import android.content.ComponentName
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.provider.MediaStore
import android.util.Log
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.addCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    private lateinit var webView: WebView
    private var proxyService: ProxyService? = null
    private var deletionActive = false
    private var filePickerCallback: android.webkit.ValueCallback<Array<Uri>>? = null

    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { /* best effort */ }

    private val filePickerLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            val cb = filePickerCallback
            filePickerCallback = null
            if (cb == null) return@registerForActivityResult
            val data = result.data
            if (result.resultCode != Activity.RESULT_OK || data == null) {
                cb.onReceiveValue(null)
                return@registerForActivityResult
            }
            val uris = mutableListOf<Uri>()
            data.clipData?.let { clip ->
                for (i in 0 until clip.itemCount) uris.add(clip.getItemAt(i).uri)
            } ?: data.data?.let { uris.add(it) }
            cb.onReceiveValue(if (uris.isEmpty()) null else uris.toTypedArray())
        }

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            proxyService = (binder as ProxyService.LocalBinder).service
            webView.loadUrl("http://127.0.0.1:${proxyService!!.port}/")
        }
        override fun onServiceDisconnected(name: ComponentName?) {
            proxyService = null
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notificationPermissionLauncher.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        }

        webView = WebView(this)
        setContentView(webView)
        setupWebView()

        onBackPressedDispatcher.addCallback(this) {
            when {
                deletionActive -> confirmExitDuringDeletion()
                webView.canGoBack() -> webView.goBack()
                else -> finish()
            }
        }

        val intent = Intent(this, ProxyService::class.java)
        ContextCompat.startForegroundService(this, intent)
        bindService(intent, serviceConnection, Context.BIND_AUTO_CREATE)
    }

    override fun onDestroy() {
        try { unbindService(serviceConnection) } catch (_: Exception) {}
        super.onDestroy()
    }

    private fun confirmExitDuringDeletion() {
        android.app.AlertDialog.Builder(this)
            .setTitle("Deletion in progress")
            .setMessage(
                "A deletion run is still going. Leaving the app now (or the app being " +
                "closed by Android) will stop it — anything already deleted stays deleted, " +
                "and the rest will remain until you reopen the app and run it again.\n\n" +
                "The foreground notification will keep it running in the background if you " +
                "just want to switch apps instead of exiting."
            )
            .setPositiveButton("Exit anyway") { _, _ -> finish() }
            .setNegativeButton("Stay", null)
            .show()
    }

    private fun setupWebView() {
        val s = webView.settings
        s.javaScriptEnabled = true
        s.domStorageEnabled = true
        s.databaseEnabled = true
        s.allowFileAccess = false
        s.allowContentAccess = false
        s.cacheMode = android.webkit.WebSettings.LOAD_DEFAULT

        // Strip the "; wv" WebView marker some sites (including, per X's own
        // developer community, in-app browsers generally) use to detect and
        // sometimes block embedded WebViews, so X's login page renders the
        // same as it would in standalone Chrome.
        s.userAgentString = s.userAgentString.replace("; wv", "")

        webView.addJavascriptInterface(AndroidBridge(), "AndroidBridge")

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val host = request.url.host ?: ""
                val isOwnOrigin = host == "127.0.0.1"
                val isXLogin = host.endsWith("x.com") || host.endsWith("twitter.com")
                if (isOwnOrigin || isXLogin) return false // load inside this WebView
                // Anything else (e.g. the console.x.com docs link) opens in the
                // user's normal browser instead of inside the app.
                return try {
                    startActivity(Intent(Intent.ACTION_VIEW, request.url))
                    true
                } catch (_: Exception) {
                    false
                }
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView,
                filePathCallback: android.webkit.ValueCallback<Array<Uri>>,
                params: FileChooserParams
            ): Boolean {
                filePickerCallback?.onReceiveValue(null)
                filePickerCallback = filePathCallback
                val intent = params.createIntent()
                return try {
                    filePickerLauncher.launch(intent)
                    true
                } catch (_: Exception) {
                    filePickerCallback = null
                    false
                }
            }
        }
    }

    /** Bridge exposed to app.js as window.AndroidBridge; see the calls added
     * in assets/public/app.js for where these are used. */
    inner class AndroidBridge {
        @JavascriptInterface
        fun notifyDeletionActive(active: Boolean) {
            deletionActive = active
            runOnUiThread { proxyService?.setDeletionActive(active) }
        }

        @JavascriptInterface
        fun saveCsvLog(filename: String, csv: String) {
            CoroutineScope(Dispatchers.IO).launch {
                val ok = try {
                    val values = ContentValues().apply {
                        put(MediaStore.Downloads.DISPLAY_NAME, filename)
                        put(MediaStore.Downloads.MIME_TYPE, "text/csv")
                    }
                    val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                    if (uri != null) {
                        contentResolver.openOutputStream(uri)?.use { it.write(csv.toByteArray()) }
                        true
                    } else false
                } catch (e: Exception) {
                    Log.e("TweetDelete", "saveCsvLog failed", e)
                    false
                }
                runOnUiThread {
                    Toast.makeText(
                        this@MainActivity,
                        if (ok) "Saved $filename to Downloads" else "Could not save $filename",
                        Toast.LENGTH_LONG
                    ).show()
                }
            }
        }
    }
}
