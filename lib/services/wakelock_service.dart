import 'package:flutter/services.dart';

/// Mantém o processo do app vivo (foreground service nativo + wake lock
/// parcial) enquanto o modo podcast está tocando, mesmo com a tela apagada
/// ou bloqueada.
///
/// Um wake lock sozinho não basta: ele evita que a CPU durma durante o
/// intervalo entre a fala de um artigo e o próximo (quando o app marca o
/// artigo como lido e avança a fila), mas não impede o sistema — sobretudo
/// gerenciadores de bateria agressivos de fabricantes como Xiaomi e
/// Samsung — de matar o processo em segundo plano pouco depois da tela
/// apagar. O lado nativo (MainActivity/PodcastPlaybackService) sobe um
/// foreground service com notificação, que é a forma padrão de sinalizar ao
/// sistema que o processo deve continuar rodando.
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
