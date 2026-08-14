package com.lsantosnet.newsflow

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationChannelCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.ServiceCompat

/**
 * Foreground service que mantém o processo do app vivo enquanto o modo
 * podcast está lendo os artigos, mesmo com a tela apagada/bloqueada.
 *
 * Um wake lock parcial sozinho (a abordagem anterior) não basta: ele evita
 * que a CPU durma, mas não impede o sistema — sobretudo gerenciadores de
 * bateria agressivos de fabricantes como Xiaomi, Samsung e Motorola — de
 * matar o processo em segundo plano pouco depois da tela apagar. Rodar como
 * foreground service com uma notificação visível é a forma padrão (usada
 * por apps de música/podcast) de sinalizar ao sistema que o processo deve
 * continuar rodando.
 */
class PodcastPlaybackService : Service() {
    private var wakeLock: PowerManager.WakeLock? = null

    companion object {
        private const val CHANNEL_ID = "podcast_playback"
        private const val NOTIFICATION_ID = 1
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        acquireWakeLock()
        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            buildNotification(),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK,
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        releaseWakeLock()
        super.onDestroy()
    }

    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) return
        val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
        val lock = powerManager.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "NewsFlow:PodcastPlayback",
        )
        lock.setReferenceCounted(false)
        lock.acquire()
        wakeLock = lock
    }

    private fun releaseWakeLock() {
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannelCompat.Builder(CHANNEL_ID, NotificationManagerCompat.IMPORTANCE_LOW)
            .setName("Modo podcast")
            .setDescription("Notificação de leitura em voz alta dos artigos")
            .build()
        NotificationManagerCompat.from(this).createNotificationChannel(channel)
    }

    private fun buildNotification(): android.app.Notification {
        val pendingIntentFlags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            android.app.PendingIntent.FLAG_IMMUTABLE
        } else {
            0
        }
        val contentIntent = packageManager.getLaunchIntentForPackage(packageName)?.let {
            android.app.PendingIntent.getActivity(this, 0, it, pendingIntentFlags)
        }
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("NewsFlow")
            .setContentText("Lendo artigos em voz alta")
            .setSmallIcon(applicationInfo.icon)
            .setContentIntent(contentIntent)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }
}
