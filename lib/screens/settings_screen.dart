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

  @override
  void initState() {
    super.initState();
    ref.read(ttsServiceProvider).getSpeechRate().then((rate) {
      if (mounted) setState(() => _speechRate = rate);
    });
  }

  @override
  Widget build(BuildContext context) {
    final ttsService = ref.read(ttsServiceProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Configurações')),
      body: _speechRate == null
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
              ],
            ),
    );
  }
}
