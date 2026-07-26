import 'package:cloud_firestore/cloud_firestore.dart';

import '../models/article.dart';

/// Encapsula o acesso à coleção `articles` do Firestore.
///
/// A ordenação principal do feed usa (relevance_score desc, curated_at desc),
/// o mesmo par de campos do índice composto definido em `firestore.indexes.json` —
/// assim o feed sempre mostra primeiro o que é mais relevante e, em caso de
/// empate, o mais recente.
class FirestoreService {
  FirestoreService({FirebaseFirestore? firestore}) : _firestore = firestore ?? FirebaseFirestore.instance;

  final FirebaseFirestore _firestore;

  static const int pageSize = 15;

  CollectionReference<Map<String, dynamic>> get _articles => _firestore.collection('articles');

  Query<Map<String, dynamic>> _baseQuery() {
    return _articles
        .orderBy('relevance_score', descending: true)
        .orderBy('curated_at', descending: true);
  }

  /// Busca uma página de artigos. Passe [startAfter] com o último documento
  /// da página anterior para implementar infinite scroll.
  Future<({List<Article> articles, DocumentSnapshot<Map<String, dynamic>>? lastDoc})> fetchPage({
    DocumentSnapshot<Map<String, dynamic>>? startAfter,
    int limit = pageSize,
  }) async {
    var query = _baseQuery().limit(limit);
    if (startAfter != null) {
      query = query.startAfterDocument(startAfter);
    }

    final snapshot = await query.get();
    final articles = snapshot.docs.map(Article.fromFirestore).toList();
    final lastDoc = snapshot.docs.isNotEmpty ? snapshot.docs.last : null;

    return (articles: articles, lastDoc: lastDoc);
  }

  Future<void> markAsRead(String articleId) {
    return _articles.doc(articleId).update({'read': true});
  }

  Future<void> setFavorite(String articleId, bool favorite) {
    return _articles.doc(articleId).update({'favorite': favorite});
  }

  /// Conta, direto no servidor (sem baixar os documentos), quantos artigos
  /// ainda estão marcados como não lidos no Firestore.
  Future<int> countUnread() async {
    final snapshot = await _articles.where('read', isEqualTo: false).count().get();
    return snapshot.count ?? 0;
  }
}
