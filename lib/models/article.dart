import 'package:cloud_firestore/cloud_firestore.dart';

/// Outra fonte que publicou a mesma notícia (agrupada pelo dedupe semântico
/// do pipeline — ver `curadoria/semantic.py`).
class ArticleSource {
  final String name;
  final String url;

  const ArticleSource({required this.name, required this.url});

  static ArticleSource? fromMap(Object? raw) {
    if (raw is! Map) return null;
    final url = raw['url'];
    if (url is! String || url.isEmpty) return null;
    return ArticleSource(name: (raw['source_name'] as String?) ?? url, url: url);
  }
}

class Article {
  final String id;
  final String title;
  final String titleHash;
  final String sourceUrl;
  final String sourceName;
  final String technicalSummary;
  final int relevanceScore;
  final List<String> tags;
  final String ttsText;
  final DateTime? publishedAt;
  final DateTime? curatedAt;
  final bool read;
  final bool favorite;
  final List<ArticleSource> alsoCoveredBy;

  const Article({
    required this.id,
    required this.title,
    required this.titleHash,
    required this.sourceUrl,
    required this.sourceName,
    required this.technicalSummary,
    required this.relevanceScore,
    required this.tags,
    required this.ttsText,
    required this.publishedAt,
    required this.curatedAt,
    required this.read,
    required this.favorite,
    this.alsoCoveredBy = const [],
  });

  factory Article.fromFirestore(DocumentSnapshot<Map<String, dynamic>> doc) {
    final data = doc.data() ?? <String, dynamic>{};
    return Article(
      id: doc.id,
      title: (data['title'] as String?) ?? '',
      titleHash: (data['title_hash'] as String?) ?? '',
      sourceUrl: (data['source_url'] as String?) ?? '',
      sourceName: (data['source_name'] as String?) ?? '',
      technicalSummary: (data['technical_summary'] as String?) ?? '',
      relevanceScore: (data['relevance_score'] as num?)?.toInt() ?? 0,
      tags: (data['tags'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? const [],
      ttsText: (data['tts_text'] as String?) ?? '',
      publishedAt: (data['published_at'] as Timestamp?)?.toDate(),
      curatedAt: (data['curated_at'] as Timestamp?)?.toDate(),
      read: (data['read'] as bool?) ?? false,
      favorite: (data['favorite'] as bool?) ?? false,
      alsoCoveredBy: (data['also_covered_by'] as List<dynamic>?)
              ?.map(ArticleSource.fromMap)
              .whereType<ArticleSource>()
              .toList() ??
          const [],
    );
  }

  Article copyWith({bool? read, bool? favorite}) {
    return Article(
      id: id,
      title: title,
      titleHash: titleHash,
      sourceUrl: sourceUrl,
      sourceName: sourceName,
      technicalSummary: technicalSummary,
      relevanceScore: relevanceScore,
      tags: tags,
      ttsText: ttsText,
      publishedAt: publishedAt,
      curatedAt: curatedAt,
      read: read ?? this.read,
      favorite: favorite ?? this.favorite,
      alsoCoveredBy: alsoCoveredBy,
    );
  }
}
