package com.lsantosnet.newsflow

import android.content.Context
import android.os.PowerManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val wakelockChannel = "com.lsantosnet.newsflow/wakelock"
    private var wakeLock: PowerManager.WakeLock? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, wakelockChannel).setMethodCallHandler { call, result ->
            when (call.method) {
                "acquire" -> {
                    acquireWakeLock()
                    result.success(null)
                }
                "release" -> {
                    releaseWakeLock()
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }
    }

    // Wake lock parcial: mantém a CPU rodando (sem ligar a tela) enquanto o
    // modo podcast está tocando, para o TTS não ser suspenso pelo Doze/App
    // Standby com o aparelho bloqueado.
    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) return
        val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
        val lock = powerManager.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "NewsFlow:PodcastPlayback",
        )
        lock.setReferenceCounted(false)
        // Sem timeout: o modo podcast controla o ciclo de vida explicitamente
        // (acquire ao tocar/retomar, release ao pausar/parar) e o onDestroy
        // abaixo libera como rede de segurança se a activity for encerrada.
        lock.acquire()
        wakeLock = lock
    }

    private fun releaseWakeLock() {
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
    }

    override fun onDestroy() {
        releaseWakeLock()
        super.onDestroy()
    }
}
