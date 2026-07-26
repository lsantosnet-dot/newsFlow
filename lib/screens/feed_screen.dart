import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/article.dart';
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

  List<Article> _applyTagFilter(List<Article> articles, String? tag) {
    if (tag == null) return articles;
    return articles.where((a) => a.tags.contains(tag)).toList();
  }

  List<Article> _applyReadFilter(List<Article> articles, ReadFilterOption filter) {
    switch (filter) {
      case ReadFilterOption.unread:
        return articles.where((a) => !a.read).toList();
      case ReadFilterOption.read:
        return articles.where((a) => a.read).toList();
      case ReadFilterOption.all:
        return articles;
    }
  }

  List<Article> _sortByDate(List<Article> articles, ArticleSortOrder order) {
    final sorted = [...articles];
    sorted.sort((a, b) {
      final dateA = a.curatedAt;
      final dateB = b.curatedAt;
      if (dateA == null && dateB == null) return 0;
      if (dateA == null) return 1;
      if (dateB == null) return -1;
      return order == ArticleSortOrder.dateAsc ? dateA.compareTo(dateB) : dateB.compareTo(dateA);
    });
    return sorted;
  }

  @override
  Widget build(BuildContext context) {
    final feedState = ref.watch(articleFeedProvider);
    final selectedTag = ref.watch(selectedTagProvider);
    final sortOrder = ref.watch(articleSortOrderProvider);
    final readFilter = ref.watch(readFilterProvider);

    var filtered = _applyTagFilter(feedState.articles, selectedTag);
    filtered = _applyReadFilter(filtered, readFilter);
    filtered = _sortByDate(filtered, sortOrder);

    return Scaffold(
      appBar: AppBar(
        title: const Text('NewsFlow'),
        actions: [
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
        ],
      ),
    );
  }
}
