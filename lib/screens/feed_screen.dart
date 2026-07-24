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

  @override
  Widget build(BuildContext context) {
    final feedState = ref.watch(articleFeedProvider);
    final selectedTag = ref.watch(selectedTagProvider);
    final filtered = _applyTagFilter(feedState.articles, selectedTag);

    return Scaffold(
      appBar: AppBar(
        title: const Text('NewsFlow'),
        actions: [
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
