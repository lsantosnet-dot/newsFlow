package com.lsantosnet.newsflow

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val wakelockChannel = "com.lsantosnet.newsflow/wakelock"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Sem essa permissão (Android 13+), o foreground service do modo
        // podcast continua rodando normalmente, só que sem mostrar a
        // notificação de "lendo artigos".
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1001)
        }
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, wakelockChannel).setMethodCallHandler { call, result ->
            when (call.method) {
                "acquire" -> {
                    startPlaybackService()
                    result.success(null)
                }
                "release" -> {
                    stopPlaybackService()
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }
    }

    // Modo podcast tocando/retomando: sobe o foreground service que segura o
    // wake lock e a notificação (ver PodcastPlaybackService), mantendo o
    // processo do app vivo mesmo com a tela apagada/bloqueada.
    private fun startPlaybackService() {
        ContextCompat.startForegroundService(this, Intent(this, PodcastPlaybackService::class.java))
    }

    // Modo podcast pausado/parado: encerra o foreground service e libera o
    // wake lock.
    private fun stopPlaybackService() {
        stopService(Intent(this, PodcastPlaybackService::class.java))
    }
}
