import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/article.dart';
import '../services/firestore_service.dart';
import '../services/tts_service.dart';

final firestoreServiceProvider = Provider<FirestoreService>((ref) => FirestoreService());

final ttsServiceProvider = Provider<TtsService>((ref) {
  final service = TtsService();
  ref.onDispose(service.dispose);
  return service;
});

/// Tag selecionada para filtrar o feed (extraída dos artigos já carregados).
final selectedTagProvider = StateProvider<String?>((ref) => null);

/// Ordenação por data de gravação (curated_at) do feed.
enum ArticleSortOrder { dateDesc, dateAsc }

final articleSortOrderProvider = StateProvider<ArticleSortOrder>((ref) => ArticleSortOrder.dateDesc);

/// Filtro por status de leitura, combinável com tag e ordenação.
enum ReadFilterOption { all, unread, read }

final readFilterProvider = StateProvider<ReadFilterOption>((ref) => ReadFilterOption.all);

/// ID do artigo cujo áudio está tocando no momento (garante um único player ativo).
final currentlyPlayingArticleIdProvider = StateProvider<String?>((ref) => null);

class ArticleFeedState {
  const ArticleFeedState({
    this.articles = const [],
    this.lastDoc,
    this.hasMore = true,
    this.isLoading = false,
    this.error,
  });

  final List<Article> articles;
  final DocumentSnapshot<Map<String, dynamic>>? lastDoc;
  final bool hasMore;
  final bool isLoading;
  final String? error;

  /// Tags únicas extraídas dos artigos já carregados, para os chips de filtro.
  List<String> get availableTags {
    final tags = <String>{};
    for (final article in articles) {
      tags.addAll(article.tags);
    }
    final sorted = tags.toList()..sort();
    return sorted;
  }

  ArticleFeedState copyWith({
    List<Article>? articles,
    DocumentSnapshot<Map<String, dynamic>>? lastDoc,
    bool? hasMore,
    bool? isLoading,
    String? error,
  }) {
    return ArticleFeedState(
      articles: articles ?? this.articles,
      lastDoc: lastDoc ?? this.lastDoc,
      hasMore: hasMore ?? this.hasMore,
      isLoading: isLoading ?? this.isLoading,
      error: error,
    );
  }
}

class ArticleFeedNotifier extends StateNotifier<ArticleFeedState> {
  ArticleFeedNotifier(this._service) : super(const ArticleFeedState()) {
    loadInitial();
  }

  final FirestoreService _service;

  Future<void> loadInitial() async {
    state = state.copyWith(isLoading: true, error: null);
    try {
      final result = await _service.fetchPage();
      state = ArticleFeedState(
        articles: result.articles,
        lastDoc: result.lastDoc,
        hasMore: result.articles.length == FirestoreService.pageSize,
        isLoading: false,
      );
    } catch (e) {
      state = state.copyWith(isLoading: false, error: e.toString());
    }
  }

  Future<void> loadMore() async {
    if (state.isLoading || !state.hasMore) return;
    state = state.copyWith(isLoading: true, error: null);
    try {
      final result = await _service.fetchPage(startAfter: state.lastDoc);
      state = state.copyWith(
        articles: [...state.articles, ...result.articles],
        lastDoc: result.lastDoc ?? state.lastDoc,
        hasMore: result.articles.length == FirestoreService.pageSize,
        isLoading: false,
      );
    } catch (e) {
      state = state.copyWith(isLoading: false, error: e.toString());
    }
  }

  Future<void> refresh() => loadInitial();

  void markAsReadLocally(String articleId) {
    state = state.copyWith(
      articles: [
        for (final article in state.articles)
          if (article.id == articleId) article.copyWith(read: true) else article,
      ],
    );
  }

  Future<void> markAsRead(String articleId) async {
    markAsReadLocally(articleId);
    await _service.markAsRead(articleId);
  }
}

final articleFeedProvider = StateNotifierProvider<ArticleFeedNotifier, ArticleFeedState>((ref) {
  return ArticleFeedNotifier(ref.watch(firestoreServiceProvider));
});
