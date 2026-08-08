import 'package:flutter/services.dart';

/// Mantém a CPU acordada (wake lock parcial do Android) enquanto o modo
/// podcast está tocando, mesmo com a tela desligada.
///
/// Sem isso, o Android pode suspender o processo do app (Doze/App Standby)
/// nos intervalos entre a fala de um artigo e o próximo — a síntese de voz
/// em si mantém a CPU acordada enquanto fala, mas o app usa esse intervalo
/// para marcar o artigo como lido e avançar a fila, e é aí que o sistema
/// pode adormecer o processo se nada estiver segurando um wake lock.
class WakelockService {
  static const _channel = MethodChannel('com.lsantosnet.newsflow/wakelock');

  Future<void> acquire() async {
    try {
      await _channel.invokeMethod('acquire');
    } on PlatformException {
      // Sem wake lock nativo disponível; a leitura continua, só que sujeita
      // a ser suspensa pelo sistema com a tela desligada.
    }
  }

  Future<void> release() async {
    try {
      await _channel.invokeMethod('release');
    } on PlatformException {
      // Nada a fazer — se o acquire falhou, o release também vai falhar.
    }
  }
}
