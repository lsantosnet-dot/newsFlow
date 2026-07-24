import 'dart:async';

import 'package:flutter_tts/flutter_tts.dart';
import 'package:shared_preferences/shared_preferences.dart';

enum TtsPlaybackState { stopped, playing, paused }

/// Encapsula o `flutter_tts`, configurado para pt-BR (os textos curados vêm
/// em português). Usa apenas o motor de TTS nativo do Android — nenhum áudio
/// é gerado ou armazenado na nuvem.
class TtsService {
  TtsService() {
    _flutterTts = FlutterTts();
  }

  static const String _speechRateKey = 'tts_speech_rate';
  static const double defaultSpeechRate = 0.5;

  late final FlutterTts _flutterTts;
  bool _initialized = false;
  bool _isAvailable = true;

  final _stateController = StreamController<TtsPlaybackState>.broadcast();
  final _progressController = StreamController<double>.broadcast();

  Stream<TtsPlaybackState> get stateStream => _stateController.stream;

  /// Progresso de 0.0 a 1.0 dentro do texto atualmente falado.
  Stream<double> get progressStream => _progressController.stream;

  bool get isAvailable => _isAvailable;

  Future<void> init() async {
    if (_initialized) return;
    _initialized = true;

    try {
      await _flutterTts.setLanguage('pt-BR');
      await _flutterTts.setSpeechRate(await getSpeechRate());
      await _flutterTts.setVolume(1.0);
      await _flutterTts.setPitch(1.0);

      final languages = await _flutterTts.isLanguageAvailable('pt-BR');
      _isAvailable = languages == true;
    } catch (_) {
      _isAvailable = false;
    }

    _flutterTts.setStartHandler(() => _stateController.add(TtsPlaybackState.playing));
    _flutterTts.setCompletionHandler(() {
      _stateController.add(TtsPlaybackState.stopped);
      _progressController.add(0.0);
    });
    _flutterTts.setCancelHandler(() => _stateController.add(TtsPlaybackState.stopped));
    _flutterTts.setPauseHandler(() => _stateController.add(TtsPlaybackState.paused));
    _flutterTts.setContinueHandler(() => _stateController.add(TtsPlaybackState.playing));
    _flutterTts.setErrorHandler((_) => _stateController.add(TtsPlaybackState.stopped));

    _flutterTts.setProgressHandler((text, start, end, word) {
      if (text.isEmpty) return;
      _progressController.add((end / text.length).clamp(0.0, 1.0));
    });
  }

  Future<void> speak(String text) async {
    if (!_isAvailable) {
      throw StateError('Nenhum motor de TTS em português disponível neste aparelho.');
    }
    await init();
    await _flutterTts.stop();
    await _flutterTts.speak(text);
  }

  /// Tenta pausar a leitura. Em aparelhos/motores Android que não suportam
  /// pausa nativa, isso efetivamente encerra a leitura (limitação do motor,
  /// não do app).
  Future<void> pause() async {
    await _flutterTts.pause();
  }

  Future<void> stop() async {
    await _flutterTts.stop();
    _stateController.add(TtsPlaybackState.stopped);
    _progressController.add(0.0);
  }

  Future<double> getSpeechRate() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getDouble(_speechRateKey) ?? defaultSpeechRate;
  }

  Future<void> setSpeechRate(double rate) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_speechRateKey, rate);
    await _flutterTts.setSpeechRate(rate);
  }

  void dispose() {
    _stateController.close();
    _progressController.close();
  }
}
