import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../models/article.dart';
import '../providers/providers.dart';
import '../services/tts_service.dart';

class ArticleCard extends ConsumerWidget {
  const ArticleCard({super.key, required this.article, required this.onTap});

  final Article article;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final isPlayingThis = ref.watch(currentlyPlayingArticleIdProvider) == article.id;

    return Opacity(
      opacity: article.read ? 0.55 : 1.0,
      child: Card(
        margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(12),
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        article.title,
                        style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold),
                        maxLines: 3,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    IconButton(
                      icon: Icon(article.favorite ? Icons.star : Icons.star_border),
                      color: article.favorite ? theme.colorScheme.tertiary : null,
                      tooltip: article.favorite ? 'Remover dos favoritos' : 'Adicionar aos favoritos',
                      onPressed: () => ref.read(articleFeedProvider.notifier).toggleFavorite(article.id),
                    ),
                    _PlayPauseButton(article: article, isPlayingThis: isPlayingThis),
                  ],
                ),
                const SizedBox(height: 8),
                Text(
                  article.technicalSummary,
                  style: theme.textTheme.bodyMedium,
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 10),
                if (article.tags.isNotEmpty)
                  Wrap(
                    spacing: 6,
                    runSpacing: 4,
                    children: [
                      for (final tag in article.tags)
                        Chip(
                          label: Text(tag, style: theme.textTheme.labelSmall),
                          visualDensity: VisualDensity.compact,
                          materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                        ),
                    ],
                  ),
                const SizedBox(height: 8),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      article.sourceName,
                      style: theme.textTheme.labelSmall?.copyWith(color: theme.colorScheme.secondary),
                    ),
                    Row(
                      children: [
                        Icon(Icons.bolt, size: 14, color: theme.colorScheme.tertiary),
                        const SizedBox(width: 2),
                        Text('${article.relevanceScore}', style: theme.textTheme.labelSmall),
                        const SizedBox(width: 10),
                        if (article.curatedAt != null)
                          Text(
                            DateFormat('dd/MM HH:mm').format(article.curatedAt!),
                            style: theme.textTheme.labelSmall,
                          ),
                      ],
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _PlayPauseButton extends ConsumerWidget {
  const _PlayPauseButton({required this.article, required this.isPlayingThis});

  final Article article;
  final bool isPlayingThis;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ttsService = ref.read(ttsServiceProvider);

    return StreamBuilder<TtsPlaybackState>(
      stream: ttsService.stateStream,
      initialData: TtsPlaybackState.stopped,
      builder: (context, snapshot) {
        final state = isPlayingThis ? (snapshot.data ?? TtsPlaybackState.stopped) : TtsPlaybackState.stopped;
        final icon = state == TtsPlaybackState.playing ? Icons.pause_circle_filled : Icons.play_circle_fill;

        return IconButton(
          icon: Icon(icon, size: 32),
          onPressed: () async {
            final notifier = ref.read(currentlyPlayingArticleIdProvider.notifier);
            if (state == TtsPlaybackState.playing) {
              await ttsService.pause();
              return;
            }
            if (ref.read(podcastProvider).isActive) {
              await ref.read(podcastProvider.notifier).stop();
            }
            notifier.state = article.id;
            try {
              await ttsService.speak(article.ttsText);
            } catch (e) {
              if (context.mounted) {
                ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
              }
            }
          },
        );
      },
    );
  }
}
