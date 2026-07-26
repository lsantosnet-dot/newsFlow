import 'dart:async';

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

/// Filtro "somente favoritos", combinável com tag, status de leitura e ordenação.
final favoritesOnlyProvider = StateProvider<bool>((ref) => false);

/// ID do artigo cujo áudio está tocando no momento (garante um único player ativo).
final currentlyPlayingArticleIdProvider = StateProvider<String?>((ref) => null);

/// Contagem de artigos não lidos direto no Firestore (independe da paginação
/// do feed). Invalidado sempre que um artigo é marcado como lido ou o feed é
/// atualizado, para refletir o estado real do servidor.
final unreadCountProvider = FutureProvider<int>((ref) {
  return ref.watch(firestoreServiceProvider).countUnread();
});

/// Artigos do feed já carregado, na mesma ordem/filtro exibidos na tela
/// (tag selecionada, filtro de lidos e ordenação por data). É a lista usada
/// tanto pelo `FeedScreen` quanto pelo modo podcast, para que ambos andem
/// sempre na mesma ordem.
final filteredArticlesProvider = Provider<List<Article>>((ref) {
  final feedState = ref.watch(articleFeedProvider);
  final selectedTag = ref.watch(selectedTagProvider);
  final readFilter = ref.watch(readFilterProvider);
  final favoritesOnly = ref.watch(favoritesOnlyProvider);
  final sortOrder = ref.watch(articleSortOrderProvider);

  var articles = feedState.articles;
  if (selectedTag != null) {
    articles = articles.where((a) => a.tags.contains(selectedTag)).toList();
  }
  articles = switch (readFilter) {
    ReadFilterOption.unread => articles.where((a) => !a.read).toList(),
    ReadFilterOption.read => articles.where((a) => a.read).toList(),
    ReadFilterOption.all => articles,
  };
  if (favoritesOnly) {
    articles = articles.where((a) => a.favorite).toList();
  }

  final sorted = [...articles];
  sorted.sort((a, b) {
    final dateA = a.curatedAt;
    final dateB = b.curatedAt;
    if (dateA == null && dateB == null) return 0;
    if (dateA == null) return 1;
    if (dateB == null) return -1;
    return sortOrder == ArticleSortOrder.dateAsc ? dateA.compareTo(dateB) : dateB.compareTo(dateA);
  });
  return sorted;
});

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
  ArticleFeedNotifier(this._service, this._ref) : super(const ArticleFeedState()) {
    loadInitial();
  }

  final FirestoreService _service;
  final Ref _ref;

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
      _ref.invalidate(unreadCountProvider);
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
    _ref.invalidate(unreadCountProvider);
  }

  Future<void> toggleFavorite(String articleId) async {
    final current = state.articles.firstWhere((a) => a.id == articleId).favorite;
    final next = !current;
    state = state.copyWith(
      articles: [
        for (final article in state.articles)
          if (article.id == articleId) article.copyWith(favorite: next) else article,
      ],
    );
    await _service.setFavorite(articleId, next);
  }
}

final articleFeedProvider = StateNotifierProvider<ArticleFeedNotifier, ArticleFeedState>((ref) {
  return ArticleFeedNotifier(ref.watch(firestoreServiceProvider), ref);
});

/// Estado do modo podcast: leitura contínua dos artigos, um após o outro, na
/// ordem atual do feed.
class PodcastState {
  const PodcastState({this.isActive = false, this.currentArticleId});

  final bool isActive;
  final String? currentArticleId;
}

class PodcastNotifier extends StateNotifier<PodcastState> {
  PodcastNotifier(this._ref) : super(const PodcastState()) {
    _completionSub = _ref.read(ttsServiceProvider).onCompletionStream.listen((_) => _playNext());
  }

  final Ref _ref;
  late final StreamSubscription<void> _completionSub;
  List<Article> _queue = const [];
  int _index = -1;

  /// Inicia o modo podcast a partir do primeiro artigo da ordem atual do
  /// feed (respeitando tag/filtro/ordenação já aplicados na tela).
  Future<void> start() async {
    final articles = _ref.read(filteredArticlesProvider);
    if (articles.isEmpty) return;
    _queue = articles;
    _index = -1;
    state = const PodcastState(isActive: true);
    await _playNext();
  }

  Future<void> stop() async {
    _queue = const [];
    _index = -1;
    state = const PodcastState();
    await _ref.read(ttsServiceProvider).stop();
    _ref.read(currentlyPlayingArticleIdProvider.notifier).state = null;
  }

  Future<void> _playNext() async {
    if (!state.isActive) return;
    _index++;
    if (_index >= _queue.length) {
      await stop();
      return;
    }

    final article = _queue[_index];
    state = PodcastState(isActive: true, currentArticleId: article.id);
    _ref.read(currentlyPlayingArticleIdProvider.notifier).state = article.id;
    if (!article.read) {
      unawaited(_ref.read(articleFeedProvider.notifier).markAsRead(article.id));
    }

    try {
      await _ref.read(ttsServiceProvider).speak(article.ttsText);
    } catch (_) {
      // Motor de TTS falhou para este artigo (ex.: texto vazio); pula para o próximo.
      await _playNext();
    }
  }

  @override
  void dispose() {
    _completionSub.cancel();
    super.dispose();
  }
}

final podcastProvider = StateNotifierProvider<PodcastNotifier, PodcastState>((ref) {
  return PodcastNotifier(ref);
});
