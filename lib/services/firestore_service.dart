import 'package:cloud_firestore/cloud_firestore.dart';

import '../models/article.dart';

/// Encapsula o acesso à coleção `articles` do Firestore.
///
/// Todas as queries são escopadas pelo `profile_id` do perfil ativo: os artigos
/// dos outros perfis continuam gravados, mas fora do feed. A ordenação principal
/// usa (relevance_score desc, curated_at desc), o mesmo trio de campos do índice
/// composto definido em `firestore.indexes.json` — assim o feed sempre mostra
/// primeiro o que é mais relevante e, em caso de empate, o mais recente.
class FirestoreService {
  FirestoreService({FirebaseFirestore? firestore}) : _firestore = firestore ?? FirebaseFirestore.instance;

  final FirebaseFirestore _firestore;

  static const int pageSize = 15;

  CollectionReference<Map<String, dynamic>> get _articles => _firestore.collection('articles');

  Query<Map<String, dynamic>> _baseQuery(String profileId) {
    return _articles
        .where('profile_id', isEqualTo: profileId)
        .orderBy('relevance_score', descending: true)
        .orderBy('curated_at', descending: true);
  }

  /// Busca uma página de artigos do perfil. Passe [startAfter] com o último
  /// documento da página anterior para implementar infinite scroll.
  Future<({List<Article> articles, DocumentSnapshot<Map<String, dynamic>>? lastDoc})> fetchPage({
    required String profileId,
    DocumentSnapshot<Map<String, dynamic>>? startAfter,
    int limit = pageSize,
  }) async {
    var query = _baseQuery(profileId).limit(limit);
    if (startAfter != null) {
      query = query.startAfterDocument(startAfter);
    }

    final snapshot = await query.get();
    final articles = snapshot.docs.map(Article.fromFirestore).toList();
    final lastDoc = snapshot.docs.isNotEmpty ? snapshot.docs.last : null;

    return (articles: articles, lastDoc: lastDoc);
  }

  Future<void> markAsRead(String articleId) {
    return _articles.doc(articleId).update({
      'read': true,
      'read_at': FieldValue.serverTimestamp(),
    });
  }

  Future<void> setFavorite(String articleId, bool favorite) {
    return _articles.doc(articleId).update({'favorite': favorite});
  }

  /// Conta, direto no servidor (sem baixar os documentos), quantos artigos do
  /// perfil ainda estão marcados como não lidos.
  Future<int> countUnread(String profileId) async {
    final snapshot =
        await _articles.where('profile_id', isEqualTo: profileId).where('read', isEqualTo: false).count().get();
    return snapshot.count ?? 0;
  }

  /// Total de artigos e quantos não lidos, para um perfil.
  ///
  /// Usa `count()` agregado: o servidor devolve só os números, sem baixar os
  /// documentos — barato o bastante para chamar por perfil ao abrir o seletor.
  Future<ProfileCounts> countForProfile(String profileId) async {
    final byProfile = _articles.where('profile_id', isEqualTo: profileId);
    final results = await Future.wait([
      byProfile.count().get(),
      byProfile.where('read', isEqualTo: false).count().get(),
    ]);

    return ProfileCounts(
      total: results[0].count ?? 0,
      unread: results[1].count ?? 0,
    );
  }
}

/// Contagem de artigos de um perfil, exibida no seletor e na lista de perfis.
class ProfileCounts {
  const ProfileCounts({required this.total, required this.unread});

  final int total;
  final int unread;

  static const empty = ProfileCounts(total: 0, unread: 0);

  /// Rótulo curto: "12 não lidos de 40", ou só o total quando tudo foi lido.
  String get label {
    if (total == 0) return 'nenhum artigo';
    if (unread == 0) return '$total ${total == 1 ? 'artigo' : 'artigos'} • tudo lido';
    return '$unread não ${unread == 1 ? 'lido' : 'lidos'} de $total';
  }
}
