import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../providers/providers.dart';

class SettingsScreen extends ConsumerStatefulWidget {
  const SettingsScreen({super.key});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> {
  double? _speechRate;
  double? _pitch;

  @override
  void initState() {
    super.initState();
    final ttsService = ref.read(ttsServiceProvider);
    ttsService.getSpeechRate().then((rate) {
      if (mounted) setState(() => _speechRate = rate);
    });
    ttsService.getPitch().then((pitch) {
      if (mounted) setState(() => _pitch = pitch);
    });
  }

  @override
  Widget build(BuildContext context) {
    final ttsService = ref.read(ttsServiceProvider);
    final isLoading = _speechRate == null || _pitch == null;

    return Scaffold(
      appBar: AppBar(title: const Text('Configurações')),
      body: isLoading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text('Velocidade da leitura (pt-BR)', style: Theme.of(context).textTheme.titleMedium),
                Slider(
                  value: _speechRate!,
                  min: 0.1,
                  max: 1.0,
                  divisions: 18,
                  label: _speechRate!.toStringAsFixed(2),
                  onChanged: (value) => setState(() => _speechRate = value),
                  onChangeEnd: (value) => ttsService.setSpeechRate(value),
                ),
                Text(
                  'Ajuste a velocidade de fala do motor de TTS nativo do Android usado para ler os artigos.',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                const SizedBox(height: 24),
                Text('Tom de voz', style: Theme.of(context).textTheme.titleMedium),
                Slider(
                  value: _pitch!,
                  min: 0.5,
                  max: 2.0,
                  divisions: 15,
                  label: _pitch!.toStringAsFixed(2),
                  onChanged: (value) => setState(() => _pitch = value),
                  onChangeEnd: (value) => ttsService.setPitch(value),
                ),
                Text(
                  'Ajuste o tom (pitch) da voz do motor de TTS. Valores menores soam mais grave, maiores soam mais agudo.',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
    );
  }
}
