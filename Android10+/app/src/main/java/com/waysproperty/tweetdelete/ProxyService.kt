package com.waysproperty.tweetdelete

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import org.json.JSONObject
import java.text.DateFormat
import java.util.Date

/**
 * Owns the embedded proxy server for as long as the app is open, and - since
 * v1.0.3 - the native [DeletionEngine] that performs the actual delete loop.
 *
 * Up to v1.0.2 the loop ran as JavaScript in the WebView and this service
 * only held a notification + wake lock. That kept the process alive but not
 * the WebView's JS timers, which Chromium throttles/suspends once the page is
 * hidden, so runs stalled at the first rate-limit wait. The loop now lives
 * here, in plain Kotlin, so backgrounding the app or turning the screen off
 * no longer matters.
 */
class ProxyService : Service() {

    companion object {
        const val CHANNEL_ID = "tweetdelete_status"
        const val NOTIF_ID = 1
        private const val TAG = "TweetDelete"
        private const val WAKE_TIMEOUT_MS = 60 * 60 * 1000L // renewed continuously while running
    }

    inner class LocalBinder : Binder() {
        val service: ProxyService get() = this@ProxyService
    }

    private val binder = LocalBinder()
    private var server: ProxyServer? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var jsFetchActive = false
    private var engineAwake = false
    private var lastNotifText = ""
    private var lastNotifAt = 0L

    lateinit var engine: DeletionEngine
        private set

    var port: Int = -1
        private set

    override fun onCreate() {
        super.onCreate()
        createChannel()
        port = ProxyServer.findFreePort()
        server = ProxyServer(applicationContext, port).also { it.start(60_000, false) }
        try {
            ServiceCompat.startForeground(
                this, NOTIF_ID, buildNotification(),
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0
            )
        } catch (e: Exception) {
            // Can happen on a sticky restart from the background on Android 12+
            // if the app is not exempt from battery optimisation.
            Log.e(TAG, "startForeground failed", e)
        }
        engine = DeletionEngine(
            applicationContext.filesDir,
            onChange = { updateNotification() },
            keepAwake = { awake -> setEngineAwake(awake) },
        )
        // Pick up a run that was interrupted by the process being killed.
        engine.restore()
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // START_STICKY: if Android kills the process despite the foreground
        // state, ask it to recreate the service; onCreate() then resumes any
        // unfinished run from its saved state.
        return START_STICKY
    }

    override fun onDestroy() {
        releaseWakeLock()
        server?.stop()
        super.onDestroy()
    }

    /** Called from the JS bridge while the fetch/review phase is in progress. */
    fun setDeletionActive(active: Boolean) {
        jsFetchActive = active
        refreshWakeLock()
        updateNotification(force = true)
    }

    private fun setEngineAwake(awake: Boolean) {
        engineAwake = awake
        refreshWakeLock()
    }

    @Synchronized
    private fun refreshWakeLock() {
        if (jsFetchActive || engineAwake) {
            val pm = getSystemService(POWER_SERVICE) as PowerManager
            val wl = wakeLock ?: pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "TweetDelete:deletionRun")
                .apply { setReferenceCounted(false) }
                .also { wakeLock = it }
            // Re-acquiring a non-reference-counted lock re-arms its timeout, so
            // a long run (16+ hours for a full 3,200-post account) is covered
            // without the old fixed 12-hour cap, while a crash can never leave
            // it held for more than an hour.
            wl.acquire(WAKE_TIMEOUT_MS)
        } else {
            releaseWakeLock()
        }
    }

    @Synchronized
    private fun releaseWakeLock() {
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                getString(R.string.notif_channel_name),
                NotificationManager.IMPORTANCE_LOW
            ).apply { description = getString(R.string.notif_channel_desc) }
            getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
    }

    @Synchronized
    private fun updateNotification(force: Boolean = false) {
        if (!::engine.isInitialized) return
        val n = buildNotification()
        val chrono = n.extras.getBoolean(Notification.EXTRA_SHOW_CHRONOMETER)
        val key = (n.extras.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: "") +
            (if (chrono) "|" + n.`when` else "")
        val now = System.currentTimeMillis()
        if (!force && key == lastNotifText && now - lastNotifAt < 30_000) return
        lastNotifText = key
        lastNotifAt = now
        getSystemService(NotificationManager::class.java).notify(NOTIF_ID, n)
    }

    private fun buildNotification(): Notification {
        val openAppIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java).setFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE
        )
        val b = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_delete)
            .setOnlyAlertOnce(true)
            .setContentIntent(openAppIntent)

        val s = if (::engine.isInitialized) JSONObject(engine.statusJson()) else null
        if (s != null && s.optString("state") == "running") {
            val total = s.optInt("total")
            val done = s.optInt("deleted") + s.optInt("failed")
            val waitUntil = s.optLong("waitUntil")
            val waiting = !s.optBoolean("paused") && waitUntil > System.currentTimeMillis()
            val at = DateFormat.getTimeInstance(DateFormat.SHORT).format(Date(waitUntil))
            val text = when {
                s.optBoolean("paused") -> "Paused — $done of $total processed"
                waiting && s.optString("waitKind") == "xdelay" ->
                    "$done of $total — waiting for X to accept resumption (retry at $at)"
                waiting && s.optString("waitKind") == "network" ->
                    "$done of $total — network problem, retrying at $at"
                waiting -> "$done of $total — rate limited, resuming at $at"
                s.optBoolean("awaitingX") -> "$done of $total — waiting for X to accept resumption"
                else -> "$done of $total processed"
            }
            if (waiting) {
                // Android draws and ticks this countdown itself (mm:ss), so it
                // stays live without the app waking up to update it.
                b.setUsesChronometer(true)
                    .setChronometerCountDown(true)
                    .setWhen(waitUntil)
                    .setShowWhen(true)
            } else {
                b.setShowWhen(false)
            }
            b.setContentTitle(getString(R.string.notif_active_title))
                .setContentText(text)
                .setProgress(total, done, false)
                .setOngoing(true)
        } else if (jsFetchActive) {
            b.setContentTitle(getString(R.string.notif_active_title))
                .setContentText(getString(R.string.notif_fetch_text))
                .setProgress(0, 0, true)
                .setOngoing(true)
        } else {
            b.setContentTitle(getString(R.string.notif_idle_title))
                .setContentText(getString(R.string.notif_idle_text))
                .setOngoing(false)
        }
        return b.build()
    }
}
