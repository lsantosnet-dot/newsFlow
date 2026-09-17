import 'package:intl/intl.dart';

import '../models/article.dart';

/// Quais blocos de cada artigo entram no corpo da newsletter.
///
/// Os três são independentes de propósito: uma newsletter só de título + link
/// cabe num email curto, enquanto a íntegra de 15 artigos vira um texto longo
/// — quem envia decide o peso na hora.
class NewsletterOptions {
  const NewsletterOptions({
    this.includeSummary = true,
    this.includeFullText = true,
    this.includeLink = true,
  });

  final bool includeSummary;
  final bool includeFullText;
  final bool includeLink;

  NewsletterOptions copyWith({
    bool? includeSummary,
    bool? includeFullText,
    bool? includeLink,
  }) {
    return NewsletterOptions(
      includeSummary: includeSummary ?? this.includeSummary,
      includeFullText: includeFullText ?? this.includeFullText,
      includeLink: includeLink ?? this.includeLink,
    );
  }
}

/// Assunto e corpo prontos para o app de email.
class Newsletter {
  const Newsletter({
    required this.subject,
    required this.body,
    required this.articleCount,
  });

  final String subject;
  final String body;
  final int articleCount;

  bool get isEmpty => articleCount == 0;
}

/// Régua entre os artigos. Caracteres de caixa em vez de markdown porque o
/// destino é o corpo de um email em texto puro — `##` e `**` chegariam
/// literais no Gmail, sem virar formatação.
const _divider = '━━━━━━━━━━━━━━━━━━━━━━━━━━━━';

/// Monta o texto da newsletter a partir dos artigos dados.
///
/// Função pura (nenhum I/O, nenhuma dependência de Flutter): recebe a lista já
/// escolhida pela tela e devolve a string. O que é "não lido" e o que é
/// "página atual" é decidido por quem chama — aqui só se formata.
///
/// [profileName] vazio omite o nome do perfil do cabeçalho; [summaryLabel]
/// acompanha o rótulo configurado no perfil ("Resumo técnico" para tecnologia,
/// "Resumo" para política), o mesmo que a tela de detalhe exibe.
Newsletter buildNewsletter({
  required List<Article> articles,
  String profileName = '',
  String summaryLabel = 'Resumo',
  NewsletterOptions options = const NewsletterOptions(),
  DateTime? generatedAt,
}) {
  final date = generatedAt ?? DateTime.now();
  final dayFormat = DateFormat('dd/MM/yyyy');
  final today = dayFormat.format(date);
  final profile = profileName.trim();
  final count = articles.length;
  final countLabel = '$count ${count == 1 ? 'artigo não lido' : 'artigos não lidos'}';

  final subject = profile.isEmpty
      ? 'NewsFlow · $today ($countLabel)'
      : 'NewsFlow — $profile · $today ($countLabel)';

  final buffer = StringBuffer()
    ..writeln(profile.isEmpty ? '📰 NewsFlow' : '📰 NewsFlow — $profile')
    ..writeln('$today · $countLabel')
    ..writeln();

  if (articles.isEmpty) {
    buffer.writeln('Nenhum artigo não lido na seleção atual do feed.');
    return Newsletter(subject: subject, body: buffer.toString().trimRight(), articleCount: 0);
  }

  for (var i = 0; i < articles.length; i++) {
    final article = articles[i];

    buffer
      ..writeln(_divider)
      ..writeln()
      ..writeln('${i + 1}. ${article.title}');

    final meta = [
      article.sourceName.trim(),
      if (article.publishedAt != null) dayFormat.format(article.publishedAt!),
    ].where((part) => part.isNotEmpty).join(' · ');
    if (meta.isNotEmpty) buffer.writeln(meta);
    buffer.writeln();

    // Cada bloco só entra se houver conteúdo: artigo sem resumo ou sem link
    // não deixa um título órfão pendurado no email.
    if (options.includeSummary && article.technicalSummary.trim().isNotEmpty) {
      buffer
        ..writeln(summaryLabel)
        ..writeln(article.technicalSummary.trim())
        ..writeln();
    }

    if (options.includeFullText && article.ttsText.trim().isNotEmpty) {
      buffer
        ..writeln('Íntegra')
        ..writeln(article.ttsText.trim())
        ..writeln();
    }

    if (options.includeLink && article.sourceUrl.trim().isNotEmpty) {
      buffer
        ..writeln('🔗 ${article.sourceUrl.trim()}')
        ..writeln();
    }
  }

  buffer
    ..writeln(_divider)
    ..writeln()
    ..writeln(
      profile.isEmpty
          ? 'Gerado pelo NewsFlow.'
          : 'Gerado pelo NewsFlow a partir do perfil "$profile".',
    );

  return Newsletter(subject: subject, body: buffer.toString().trimRight(), articleCount: count);
}
