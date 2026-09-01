package com.waysproperty.tweetdelete

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat

/**
 * Owns the embedded proxy server for as long as the app is open, and holds a
 * foreground-service notification + wake lock while a deletion run is
 * actively in progress.
 *
 * Why a foreground service at all: X paces deletions to 50 per 15 minutes
 * (see the desktop build's README), so a large account can take hours or
 * even days to fully process. Android suspends background JS execution and
 * eventually kills the process once the app is backgrounded, far more
 * aggressively than a desktop browser tab ever would. A foreground service
 * with a visible notification is the standard, supported way to tell
 * Android "this is intentionally still doing work" and get a real, if not
 * unlimited, reprieve from that. It is not a guarantee the OS will never
 * kill the process (reboots, force-stops and severe memory pressure can
 * still interrupt a run) — which is why the deletion loop in app.js is
 * designed to be safely restartable: it always re-fetches the live,
 * currently-remaining set from X's API rather than trusting a saved
 * checkpoint, so simply re-opening the app and starting again picks up
 * correctly from wherever the run actually left off.
 */
class ProxyService : Service() {

    companion object {
        const val CHANNEL_ID = "tweetdelete_status"
        const val NOTIF_ID = 1
    }

    inner class LocalBinder : Binder() {
        val service: ProxyService get() = this@ProxyService
    }

    private val binder = LocalBinder()
    private var server: ProxyServer? = null
    private var wakeLock: PowerManager.WakeLock? = null

    var port: Int = -1
        private set

    override fun onCreate() {
        super.onCreate()
        createChannel()
        port = ProxyServer.findFreePort()
        server = ProxyServer(applicationContext, port).also { it.start(60_000, false) }
        startForeground(NOTIF_ID, buildNotification(active = false))
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // START_STICKY: if Android still kills this process under memory
        // pressure despite the foreground state, ask it to recreate the
        // service (a fresh server + notification) rather than leaving the
        // app in a half-alive state with no proxy running.
        return START_STICKY
    }

    override fun onDestroy() {
        releaseWakeLock()
        server?.stop()
        super.onDestroy()
    }

    fun setDeletionActive(active: Boolean) {
        val nm = getSystemService(NotificationManager::class.java)
        nm.notify(NOTIF_ID, buildNotification(active))
        if (active) acquireWakeLock() else releaseWakeLock()
    }

    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) return
        val pm = getSystemService(POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "TweetDelete:deletionRun").apply {
            setReferenceCounted(false)
            acquire(12 * 60 * 60 * 1000L) // 12h safety cap; released explicitly on finish/cancel too.
        }
    }

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

    private fun buildNotification(active: Boolean): Notification {
        val openAppIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java).setFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE
        )
        val title = getString(if (active) R.string.notif_active_title else R.string.notif_idle_title)
        val text = getString(if (active) R.string.notif_active_text else R.string.notif_idle_text)
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_delete)
            .setContentTitle(title)
            .setContentText(text)
            .setOngoing(active)
            .setOnlyAlertOnce(true)
            .setContentIntent(openAppIntent)
            .build()
    }
}
