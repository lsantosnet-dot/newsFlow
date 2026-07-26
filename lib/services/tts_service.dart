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

  static const String _pitchKey = 'tts_pitch';
  static const double defaultPitch = 1.0;

  late final FlutterTts _flutterTts;
  bool _initialized = false;
  bool _isAvailable = true;

  /// Texto completo do artigo em reprodução (usado como referência para
  /// calcular progresso e para retomar de onde parou após um [pause]).
  String? _fullText;

  /// Deslocamento (em caracteres, dentro de [_fullText]) onde o trecho de
  /// fala atual começou — diferente de zero depois de um [resume].
  int _resumeOffset = 0;

  /// Último caractere (absoluto, em [_fullText]) que o motor já falou.
  int _lastEnd = 0;

  final _stateController = StreamController<TtsPlaybackState>.broadcast();
  final _progressController = StreamController<double>.broadcast();
  final _completionController = StreamController<void>.broadcast();

  Stream<TtsPlaybackState> get stateStream => _stateController.stream;

  /// Progresso de 0.0 a 1.0 dentro do texto atualmente falado.
  Stream<double> get progressStream => _progressController.stream;

  /// Emite um evento só quando a fala termina naturalmente (chegou ao fim do
  /// texto) — diferente de [stateStream], que também emite "stopped" quando
  /// o usuário cancela manualmente via [stop]. Usado pelo modo podcast para
  /// saber quando avançar para o próximo artigo.
  Stream<void> get onCompletionStream => _completionController.stream;

  bool get isAvailable => _isAvailable;

  Future<void> init() async {
    if (_initialized) return;
    _initialized = true;

    try {
      await _flutterTts.setLanguage('pt-BR');
      await _flutterTts.setSpeechRate(await getSpeechRate());
      await _flutterTts.setVolume(1.0);
      await _flutterTts.setPitch(await getPitch());

      final languages = await _flutterTts.isLanguageAvailable('pt-BR');
      _isAvailable = languages == true;
    } catch (_) {
      _isAvailable = false;
    }

    _flutterTts.setStartHandler(() => _stateController.add(TtsPlaybackState.playing));
    _flutterTts.setCompletionHandler(() {
      _stateController.add(TtsPlaybackState.stopped);
      _progressController.add(0.0);
      _completionController.add(null);
    });
    _flutterTts.setCancelHandler(() => _stateController.add(TtsPlaybackState.stopped));
    _flutterTts.setErrorHandler((_) => _stateController.add(TtsPlaybackState.stopped));

    _flutterTts.setProgressHandler((text, start, end, word) {
      final fullText = _fullText;
      if (fullText == null || fullText.isEmpty) return;
      _lastEnd = _resumeOffset + end;
      _progressController.add((_lastEnd / fullText.length).clamp(0.0, 1.0));
    });
  }

  Future<void> speak(String text) async {
    if (!_isAvailable) {
      throw StateError('Nenhum motor de TTS em português disponível neste aparelho.');
    }
    await init();
    await _flutterTts.stop();
    _fullText = text;
    _resumeOffset = 0;
    _lastEnd = 0;
    await _flutterTts.speak(text);
  }

  /// Pausa a leitura atual. O motor de TTS nativo do Android não tem um pause
  /// real (separado de stop), então isso interrompe o áudio e guarda a
  /// posição (por palavra) para [resume] continuar dali.
  Future<void> pause() async {
    await _flutterTts.stop();
    _stateController.add(TtsPlaybackState.paused);
  }

  /// Retoma a leitura de onde [pause] parou, falando o restante do texto a
  /// partir da última palavra concluída. Se não houver nada pausado, não faz nada.
  Future<void> resume() async {
    final fullText = _fullText;
    if (fullText == null) return;

    _resumeOffset = _lastEnd.clamp(0, fullText.length);
    final remaining = fullText.substring(_resumeOffset);
    if (remaining.trim().isEmpty) {
      _stateController.add(TtsPlaybackState.stopped);
      _completionController.add(null);
      return;
    }
    await _flutterTts.speak(remaining);
  }

  Future<void> stop() async {
    await _flutterTts.stop();
    _fullText = null;
    _resumeOffset = 0;
    _lastEnd = 0;
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

  Future<double> getPitch() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getDouble(_pitchKey) ?? defaultPitch;
  }

  Future<void> setPitch(double pitch) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_pitchKey, pitch);
    await _flutterTts.setPitch(pitch);
  }

  void dispose() {
    _stateController.close();
    _progressController.close();
    _completionController.close();
  }
}
