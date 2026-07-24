import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import 'package:url_launcher/url_launcher.dart';

import '../models/article.dart';
import '../providers/providers.dart';
import '../services/tts_service.dart';

class ArticleDetailScreen extends ConsumerStatefulWidget {
  const ArticleDetailScreen({super.key, required this.article});

  final Article article;

  @override
  ConsumerState<ArticleDetailScreen> createState() => _ArticleDetailScreenState();
}

class _ArticleDetailScreenState extends ConsumerState<ArticleDetailScreen> {
  @override
  void initState() {
    super.initState();
    if (!widget.article.read) {
      Future.microtask(
        () => ref.read(articleFeedProvider.notifier).markAsRead(widget.article.id),
      );
    }
  }

  Future<void> _openOriginal() async {
    final uri = Uri.tryParse(widget.article.sourceUrl);
    if (uri == null) return;
    if (!await launchUrl(uri, mode: LaunchMode.externalApplication)) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Não foi possível abrir o link.')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final article = widget.article;
    final ttsService = ref.watch(ttsServiceProvider);
    final isPlayingThis = ref.watch(currentlyPlayingArticleIdProvider) == article.id;

    return Scaffold(
      appBar: AppBar(title: const Text('Detalhe do artigo')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(article.title, style: theme.textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.bold)),
            const SizedBox(height: 8),
            Row(
              children: [
                Text(article.sourceName, style: theme.textTheme.bodySmall),
                const SizedBox(width: 12),
                Icon(Icons.bolt, size: 14, color: theme.colorScheme.tertiary),
                const SizedBox(width: 2),
                Text('${article.relevanceScore}', style: theme.textTheme.bodySmall),
                if (article.curatedAt != null) ...[
                  const SizedBox(width: 12),
                  Text(DateFormat('dd/MM/yyyy HH:mm').format(article.curatedAt!), style: theme.textTheme.bodySmall),
                ],
              ],
            ),
            const SizedBox(height: 12),
            if (article.tags.isNotEmpty)
              Wrap(
                spacing: 6,
                runSpacing: 4,
                children: [for (final tag in article.tags) Chip(label: Text(tag))],
              ),
            const SizedBox(height: 16),
            Text(article.technicalSummary, style: theme.textTheme.bodyLarge),
            const SizedBox(height: 24),
            OutlinedButton.icon(
              onPressed: _openOriginal,
              icon: const Icon(Icons.open_in_new),
              label: const Text('Ler artigo original'),
            ),
            const SizedBox(height: 32),
            _ListenSection(article: article, ttsService: ttsService, isPlayingThis: isPlayingThis),
          ],
        ),
      ),
    );
  }
}

class _ListenSection extends ConsumerWidget {
  const _ListenSection({
    required this.article,
    required this.ttsService,
    required this.isPlayingThis,
  });

  final Article article;
  final TtsService ttsService;
  final bool isPlayingThis;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);

    return StreamBuilder<TtsPlaybackState>(
      stream: ttsService.stateStream,
      initialData: TtsPlaybackState.stopped,
      builder: (context, stateSnapshot) {
        final state = isPlayingThis ? (stateSnapshot.data ?? TtsPlaybackState.stopped) : TtsPlaybackState.stopped;

        return Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              children: [
                Text('Ouvir este artigo', style: theme.textTheme.titleMedium),
                const SizedBox(height: 12),
                StreamBuilder<double>(
                  stream: ttsService.progressStream,
                  initialData: 0.0,
                  builder: (context, progressSnapshot) {
                    final progress = isPlayingThis ? (progressSnapshot.data ?? 0.0) : 0.0;
                    return LinearProgressIndicator(value: state == TtsPlaybackState.stopped ? 0 : progress);
                  },
                ),
                const SizedBox(height: 16),
                Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    IconButton(
                      iconSize: 56,
                      icon: Icon(
                        state == TtsPlaybackState.playing ? Icons.pause_circle_filled : Icons.play_circle_fill,
                      ),
                      onPressed: () async {
                        final notifier = ref.read(currentlyPlayingArticleIdProvider.notifier);
                        try {
                          if (state == TtsPlaybackState.playing) {
                            await ttsService.pause();
                          } else {
                            notifier.state = article.id;
                            await ttsService.speak(article.ttsText);
                          }
                        } catch (e) {
                          if (context.mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
                          }
                        }
                      },
                    ),
                    const SizedBox(width: 24),
                    IconButton(
                      iconSize: 40,
                      icon: const Icon(Icons.stop_circle),
                      onPressed: state == TtsPlaybackState.stopped ? null : () => ttsService.stop(),
                    ),
                  ],
                ),
                if (!ttsService.isAvailable)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text(
                      'Nenhum motor de TTS em português encontrado neste aparelho.',
                      style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error),
                    ),
                  ),
              ],
            ),
          ),
        );
      },
    );
  }
}
