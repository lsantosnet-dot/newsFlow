import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:flutter/material.dart';

/// O que fazer com os artigos já curados quando as fontes/critérios de um
/// perfil mudam. O app apenas sinaliza a escolha em `pending_cleanup`; quem
/// executa o delete é o pipeline (Admin SDK), já que as regras do Firestore
/// proíbem `delete` no cliente.
enum ProfileCleanupMode {
  keep,
  purgeUnread,
  purgeAll;

  /// Valor gravado em `pending_cleanup` — `null` para [keep] (nada a fazer).
  String? get firestoreValue => switch (this) {
        ProfileCleanupMode.keep => null,
        ProfileCleanupMode.purgeUnread => 'purge_unread',
        ProfileCleanupMode.purgeAll => 'purge_all',
      };

  String get label => switch (this) {
        ProfileCleanupMode.keep => 'Manter tudo',
        ProfileCleanupMode.purgeUnread => 'Limpar não lidos',
        ProfileCleanupMode.purgeAll => 'Limpar tudo',
      };

  String get description => switch (this) {
        ProfileCleanupMode.keep =>
          'Os artigos atuais continuam no feed. A nova configuração vale só para os próximos.',
        ProfileCleanupMode.purgeUnread =>
          'Apaga os não lidos que foram curados com os critérios antigos. Preserva lidos e favoritos.',
        ProfileCleanupMode.purgeAll =>
          'Apaga todos os artigos deste perfil. Favoritos são sempre preservados.',
      };
}

/// Uma fonte de notícias dentro de um perfil. O `type` resolve para um
/// adaptador no pipeline Python (`SOURCE_ADAPTERS` em `ingest.py`).
class ProfileSource {
  const ProfileSource({
    required this.type,
    required this.name,
    this.url,
    this.limit,
    this.tags = const [],
    this.perTag,
    this.minScore,
    this.minComments,
    this.categories = const [],
  });

  final String type;
  final String name;
  final String? url;
  final int? limit;
  final List<String> tags;
  final int? perTag;
  final int? minScore;
  final int? minComments;
  final List<String> categories;

  bool get isRss => type == 'rss';

  /// Descrição curta para a lista de fontes na tela de edição.
  String get subtitle => switch (type) {
        'rss' => url ?? '',
        'hackernews' => 'score ≥ ${minScore ?? 50}, comentários ≥ ${minComments ?? 10}',
        'devto' => tags.join(', '),
        'arxiv' => categories.join(', '),
        _ => type,
      };

  factory ProfileSource.fromMap(Map<String, dynamic> data) {
    return ProfileSource(
      type: (data['type'] as String?) ?? 'rss',
      name: (data['name'] as String?) ?? '',
      url: data['url'] as String?,
      limit: (data['limit'] as num?)?.toInt(),
      tags: (data['tags'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? const [],
      perTag: (data['per_tag'] as num?)?.toInt(),
      minScore: (data['min_score'] as num?)?.toInt(),
      minComments: (data['min_comments'] as num?)?.toInt(),
      categories: (data['categories'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? const [],
    );
  }

  Map<String, dynamic> toMap() {
    return {
      'type': type,
      'name': name,
      if (url != null) 'url': url,
      if (limit != null) 'limit': limit,
      if (tags.isNotEmpty) 'tags': tags,
      if (perTag != null) 'per_tag': perTag,
      if (minScore != null) 'min_score': minScore,
      if (minComments != null) 'min_comments': minComments,
      if (categories.isNotEmpty) 'categories': categories,
    };
  }
}

/// Critérios de curadoria — viram o system prompt do Gemini em `build_system_prompt`.
class CurationConfig {
  const CurationConfig({
    this.persona = '',
    this.summaryLabel = 'Resumo',
    this.minScore = 75,
    this.approveCriteria = const [],
    this.rejectCriteria = const [],
    this.extraInstructions = '',
  });

  final String persona;
  final String summaryLabel;
  final int minScore;
  final List<String> approveCriteria;
  final List<String> rejectCriteria;
  final String extraInstructions;

  factory CurationConfig.fromMap(Map<String, dynamic> data) {
    return CurationConfig(
      persona: (data['persona'] as String?) ?? '',
      summaryLabel: (data['summary_label'] as String?) ?? 'Resumo',
      minScore: (data['min_score'] as num?)?.toInt() ?? 75,
      approveCriteria:
          (data['approve_criteria'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? const [],
      rejectCriteria:
          (data['reject_criteria'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? const [],
      extraInstructions: (data['extra_instructions'] as String?) ?? '',
    );
  }

  Map<String, dynamic> toMap() {
    return {
      'persona': persona,
      'summary_label': summaryLabel,
      'min_score': minScore,
      'approve_criteria': approveCriteria,
      'reject_criteria': rejectCriteria,
      'extra_instructions': extraInstructions,
    };
  }

  CurationConfig copyWith({
    String? persona,
    String? summaryLabel,
    int? minScore,
    List<String>? approveCriteria,
    List<String>? rejectCriteria,
    String? extraInstructions,
  }) {
    return CurationConfig(
      persona: persona ?? this.persona,
      summaryLabel: summaryLabel ?? this.summaryLabel,
      minScore: minScore ?? this.minScore,
      approveCriteria: approveCriteria ?? this.approveCriteria,
      rejectCriteria: rejectCriteria ?? this.rejectCriteria,
      extraInstructions: extraInstructions ?? this.extraInstructions,
    );
  }
}

/// Um perfil de curadoria: o que buscar (`sources`) e como filtrar (`curation`).
/// Apenas um perfil fica `active` por vez — é o que o pipeline roda.
class Profile {
  const Profile({
    required this.id,
    required this.name,
    required this.icon,
    required this.active,
    required this.configVersion,
    required this.sources,
    required this.curation,
    this.maxItemsPerRun = 40,
    this.dedupeWindowDays = 7,
    this.cleanupGraceHours = 48,
    this.inactiveRetentionDays = 30,
    this.pendingCleanup,
  });

  final String id;
  final String name;
  final String icon;
  final bool active;
  final int configVersion;
  final List<ProfileSource> sources;
  final CurationConfig curation;
  final int maxItemsPerRun;
  final int dedupeWindowDays;
  final int cleanupGraceHours;
  final int inactiveRetentionDays;
  final String? pendingCleanup;

  bool get hasPendingCleanup => pendingCleanup != null && pendingCleanup!.isNotEmpty;

  /// Ícone Material correspondente ao nome salvo no Firestore. Um mapa fixo em
  /// vez de `IconData` dinâmico para não quebrar o tree-shaking de ícones.
  IconData get iconData => switch (icon) {
        'code' => Icons.code,
        'account_balance' => Icons.account_balance,
        'public' => Icons.public,
        'trending_up' => Icons.trending_up,
        'science' => Icons.science,
        'sports_soccer' => Icons.sports_soccer,
        'movie' => Icons.movie,
        'health_and_safety' => Icons.health_and_safety,
        'school' => Icons.school,
        'newspaper' => Icons.newspaper,
        _ => Icons.newspaper,
      };

  /// Nomes de ícone oferecidos na tela de edição.
  static const List<String> availableIcons = [
    'newspaper',
    'code',
    'account_balance',
    'public',
    'trending_up',
    'science',
    'sports_soccer',
    'movie',
    'health_and_safety',
    'school',
  ];

  factory Profile.fromFirestore(DocumentSnapshot<Map<String, dynamic>> doc) {
    final data = doc.data() ?? <String, dynamic>{};
    return Profile.fromMap(doc.id, data);
  }

  factory Profile.fromMap(String id, Map<String, dynamic> data) {
    return Profile(
      id: id,
      name: (data['name'] as String?) ?? '',
      icon: (data['icon'] as String?) ?? 'newspaper',
      active: (data['active'] as bool?) ?? false,
      configVersion: (data['config_version'] as num?)?.toInt() ?? 1,
      sources: (data['sources'] as List<dynamic>?)
              ?.map((e) => ProfileSource.fromMap(Map<String, dynamic>.from(e as Map)))
              .toList() ??
          const [],
      curation: CurationConfig.fromMap(
        Map<String, dynamic>.from((data['curation'] as Map?) ?? <String, dynamic>{}),
      ),
      maxItemsPerRun: (data['max_items_per_run'] as num?)?.toInt() ?? 40,
      dedupeWindowDays: (data['dedupe_window_days'] as num?)?.toInt() ?? 7,
      cleanupGraceHours: (data['cleanup_grace_hours'] as num?)?.toInt() ?? 48,
      inactiveRetentionDays: (data['inactive_retention_days'] as num?)?.toInt() ?? 30,
      pendingCleanup: data['pending_cleanup'] as String?,
    );
  }

  Map<String, dynamic> toMap() {
    return {
      'name': name,
      'icon': icon,
      'active': active,
      'config_version': configVersion,
      'sources': sources.map((s) => s.toMap()).toList(),
      'curation': curation.toMap(),
      'max_items_per_run': maxItemsPerRun,
      'dedupe_window_days': dedupeWindowDays,
      'cleanup_grace_hours': cleanupGraceHours,
      'inactive_retention_days': inactiveRetentionDays,
      'pending_cleanup': pendingCleanup,
    };
  }

  Profile copyWith({
    String? id,
    String? name,
    String? icon,
    bool? active,
    int? configVersion,
    List<ProfileSource>? sources,
    CurationConfig? curation,
    int? maxItemsPerRun,
    int? dedupeWindowDays,
    int? cleanupGraceHours,
    int? inactiveRetentionDays,
    String? pendingCleanup,
  }) {
    return Profile(
      id: id ?? this.id,
      name: name ?? this.name,
      icon: icon ?? this.icon,
      active: active ?? this.active,
      configVersion: configVersion ?? this.configVersion,
      sources: sources ?? this.sources,
      curation: curation ?? this.curation,
      maxItemsPerRun: maxItemsPerRun ?? this.maxItemsPerRun,
      dedupeWindowDays: dedupeWindowDays ?? this.dedupeWindowDays,
      cleanupGraceHours: cleanupGraceHours ?? this.cleanupGraceHours,
      inactiveRetentionDays: inactiveRetentionDays ?? this.inactiveRetentionDays,
      pendingCleanup: pendingCleanup ?? this.pendingCleanup,
    );
  }
}
