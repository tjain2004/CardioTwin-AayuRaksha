package com.cardiotwin.wear

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.lifecycle.LifecycleService

/**
 * Keeps sensor collection and the backend link running while the screen is off (watch-sensor mode).
 * Mock mode does not need it: the app runs the replay while it is open.
 */
class MonitorService : LifecycleService() {

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        val type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_HEALTH else 0
        try {
            ServiceCompat.startForeground(this, NOTIFICATION_ID, notification(), type)
        } catch (e: SecurityException) {          // body-sensor permission not granted: run only while the app is open
            stopSelf()
            return START_NOT_STICKY
        }
        (application as CardioTwinApp).repository.start()
        return START_STICKY
    }

    private fun notification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Monitoring", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        return NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle("CardioTwin")
            .setContentText("Monitoring heart rate and falls")
            .setContentIntent(open)
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_WORKOUT)
            .build()
    }

    companion object {
        private const val CHANNEL = "monitoring"
        private const val NOTIFICATION_ID = 1
        const val ACTION_STOP = "com.cardiotwin.wear.STOP"

        fun start(context: Context) = context.startForegroundService(Intent(context, MonitorService::class.java))
        fun stop(context: Context) = context.startService(Intent(context, MonitorService::class.java).setAction(ACTION_STOP))
    }
}
