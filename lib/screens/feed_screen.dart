import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../providers/providers.dart';
import '../widgets/article_card.dart';
import 'article_detail_screen.dart';
import 'settings_screen.dart';

class FeedScreen extends ConsumerStatefulWidget {
  const FeedScreen({super.key});

  @override
  ConsumerState<FeedScreen> createState() => _FeedScreenState();
}

class _FeedScreenState extends ConsumerState<FeedScreen> {
  final _scrollController = ScrollController();

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScroll);
  }

  void _onScroll() {
    if (_scrollController.position.pixels >= _scrollController.position.maxScrollExtent - 300) {
      ref.read(articleFeedProvider.notifier).loadMore();
    }
  }

  @override
  void dispose() {
    _scrollController.removeListener(_onScroll);
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final feedState = ref.watch(articleFeedProvider);
    final selectedTag = ref.watch(selectedTagProvider);
    final sortOrder = ref.watch(articleSortOrderProvider);
    final readFilter = ref.watch(readFilterProvider);
    final favoritesOnly = ref.watch(favoritesOnlyProvider);
    final unreadCount = ref.watch(unreadCountProvider).asData?.value;
    final filtered = ref.watch(filteredArticlesProvider);
    final podcastState = ref.watch(podcastProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('NewsFlow'),
        actions: [
          IconButton(
            icon: Badge(
              label: Text('${unreadCount ?? 0}'),
              isLabelVisible: (unreadCount ?? 0) > 0,
              child: const Icon(Icons.mark_email_unread_outlined),
            ),
            tooltip: unreadCount == null
                ? 'Contando não lidos...'
                : '$unreadCount artigo(s) não lido(s) no Firestore',
            onPressed: () => ref.read(readFilterProvider.notifier).state = ReadFilterOption.unread,
          ),
          IconButton(
            icon: Icon(favoritesOnly ? Icons.star : Icons.star_border),
            color: favoritesOnly ? theme.colorScheme.tertiary : null,
            tooltip: favoritesOnly ? 'Mostrar todos os artigos' : 'Mostrar somente favoritos',
            onPressed: () => ref.read(favoritesOnlyProvider.notifier).state = !favoritesOnly,
          ),
          IconButton(
            icon: Icon(podcastState.isActive ? Icons.stop_circle : Icons.podcasts),
            tooltip: podcastState.isActive
                ? 'Parar modo podcast'
                : 'Modo podcast: ouvir os artigos em sequência',
            onPressed: () {
              if (podcastState.isActive) {
                ref.read(podcastProvider.notifier).stop();
              } else {
                ref.read(podcastProvider.notifier).start();
              }
            },
          ),
          IconButton(
            icon: Icon(sortOrder == ArticleSortOrder.dateDesc ? Icons.arrow_downward : Icons.arrow_upward),
            tooltip: sortOrder == ArticleSortOrder.dateDesc
                ? 'Ordenar por data: mais recentes primeiro'
                : 'Ordenar por data: mais antigos primeiro',
            onPressed: () {
              ref.read(articleSortOrderProvider.notifier).state = sortOrder == ArticleSortOrder.dateDesc
                  ? ArticleSortOrder.dateAsc
                  : ArticleSortOrder.dateDesc;
            },
          ),
          PopupMenuButton<ReadFilterOption>(
            icon: const Icon(Icons.filter_list),
            tooltip: 'Filtrar por status de leitura',
            initialValue: readFilter,
            onSelected: (value) => ref.read(readFilterProvider.notifier).state = value,
            itemBuilder: (context) => [
              CheckedPopupMenuItem(
                value: ReadFilterOption.all,
                checked: readFilter == ReadFilterOption.all,
                child: const Text('Todos'),
              ),
              CheckedPopupMenuItem(
                value: ReadFilterOption.unread,
                checked: readFilter == ReadFilterOption.unread,
                child: const Text('Não lidos'),
              ),
              CheckedPopupMenuItem(
                value: ReadFilterOption.read,
                checked: readFilter == ReadFilterOption.read,
                child: const Text('Lidos'),
              ),
            ],
          ),
          IconButton(
            icon: const Icon(Icons.settings),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => const SettingsScreen()),
            ),
          ),
        ],
      ),
      body: Column(
        children: [
          if (feedState.availableTags.isNotEmpty)
            SizedBox(
              height: 48,
              child: ListView(
                scrollDirection: Axis.horizontal,
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                children: [
                  Padding(
                    padding: const EdgeInsets.only(right: 6),
                    child: ChoiceChip(
                      label: const Text('Todas'),
                      selected: selectedTag == null,
                      onSelected: (_) => ref.read(selectedTagProvider.notifier).state = null,
                    ),
                  ),
                  for (final tag in feedState.availableTags)
                    Padding(
                      padding: const EdgeInsets.only(right: 6),
                      child: ChoiceChip(
                        label: Text(tag),
                        selected: selectedTag == tag,
                        onSelected: (_) => ref.read(selectedTagProvider.notifier).state = tag,
                      ),
                    ),
                ],
              ),
            ),
          Expanded(
            child: RefreshIndicator(
              onRefresh: () => ref.read(articleFeedProvider.notifier).refresh(),
              child: feedState.articles.isEmpty && feedState.isLoading
                  ? const Center(child: CircularProgressIndicator())
                  : feedState.articles.isEmpty
                      ? ListView(
                          children: const [
                            SizedBox(height: 120),
                            Center(child: Text('Nenhum artigo curado ainda.')),
                          ],
                        )
                      : ListView.builder(
                          controller: _scrollController,
                          itemCount: filtered.length + (feedState.hasMore ? 1 : 0),
                          itemBuilder: (context, index) {
                            if (index >= filtered.length) {
                              return const Padding(
                                padding: EdgeInsets.all(24),
                                child: Center(child: CircularProgressIndicator()),
                              );
                            }
                            final article = filtered[index];
                            return ArticleCard(
                              article: article,
                              onTap: () => Navigator.of(context).push(
                                MaterialPageRoute(
                                  builder: (_) => ArticleDetailScreen(article: article),
                                ),
                              ),
                            );
                          },
                        ),
            ),
          ),
          if (podcastState.isActive)
            _PodcastBar(currentArticleId: podcastState.currentArticleId),
        ],
      ),
    );
  }
}

class _PodcastBar extends ConsumerWidget {
  const _PodcastBar({required this.currentArticleId});

  final String? currentArticleId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final ttsService = ref.watch(ttsServiceProvider);
    final feedState = ref.watch(articleFeedProvider);
    final matches = feedState.articles.where((a) => a.id == currentArticleId);
    final title = matches.isEmpty ? 'Carregando próximo artigo...' : matches.first.title;

    return Material(
      elevation: 8,
      color: theme.colorScheme.surfaceContainerHighest,
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 8, 8),
          child: Row(
            children: [
              Icon(Icons.podcasts, color: theme.colorScheme.primary),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 4),
                    StreamBuilder<double>(
                      stream: ttsService.progressStream,
                      initialData: 0.0,
                      builder: (context, snapshot) {
                        return LinearProgressIndicator(value: snapshot.data ?? 0.0);
                      },
                    ),
                  ],
                ),
              ),
              IconButton(
                icon: const Icon(Icons.stop_circle),
                tooltip: 'Parar modo podcast',
                onPressed: () => ref.read(podcastProvider.notifier).stop(),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
