import 'package:flutter_test/flutter_test.dart';
import 'package:newsflow/models/article.dart';
import 'package:newsflow/services/newsletter_builder.dart';

Article _article({
  String id = 'a1',
  String title = 'Título do artigo',
  String summary = 'Resumo curto.',
  String ttsText = 'Texto longo do artigo.',
  String sourceUrl = 'https://exemplo.com/artigo',
  String sourceName = 'Fonte',
}) {
  return Article(
    id: id,
    title: title,
    titleHash: 'hash-$id',
    sourceUrl: sourceUrl,
    sourceName: sourceName,
    technicalSummary: summary,
    relevanceScore: 90,
    tags: const ['IA'],
    ttsText: ttsText,
    publishedAt: DateTime(2026, 9, 16),
    curatedAt: DateTime(2026, 9, 17),
    read: false,
    favorite: false,
  );
}

final _generatedAt = DateTime(2026, 9, 17);

void main() {
  group('buildNewsletter', () {
    test('monta cabeçalho, assunto e os quatro blocos de cada artigo', () {
      final newsletter = buildNewsletter(
        articles: [_article()],
        profileName: 'Tecnologia',
        summaryLabel: 'Resumo técnico',
        generatedAt: _generatedAt,
      );

      expect(newsletter.articleCount, 1);
      expect(newsletter.isEmpty, isFalse);
      expect(newsletter.subject, 'NewsFlow — Tecnologia · 17/09/2026 (1 artigo não lido)');
      expect(newsletter.body, contains('📰 NewsFlow — Tecnologia'));
      expect(newsletter.body, contains('17/09/2026 · 1 artigo não lido'));
      expect(newsletter.body, contains('1. Título do artigo'));
      expect(newsletter.body, contains('Fonte · 16/09/2026'));
      expect(newsletter.body, contains('Resumo técnico'));
      expect(newsletter.body, contains('Resumo curto.'));
      expect(newsletter.body, contains('Íntegra'));
      expect(newsletter.body, contains('Texto longo do artigo.'));
      expect(newsletter.body, contains('🔗 https://exemplo.com/artigo'));
      expect(newsletter.body, contains('perfil "Tecnologia"'));
    });

    test('numera os artigos na ordem recebida e pluraliza a contagem', () {
      final newsletter = buildNewsletter(
        articles: [
          _article(id: 'a1', title: 'Primeiro'),
          _article(id: 'a2', title: 'Segundo'),
        ],
        profileName: 'Tecnologia',
        generatedAt: _generatedAt,
      );

      expect(newsletter.articleCount, 2);
      expect(newsletter.body, contains('2 artigos não lidos'));
      expect(newsletter.body.indexOf('1. Primeiro'), lessThan(newsletter.body.indexOf('2. Segundo')));
    });

    test('os toggles removem os blocos correspondentes', () {
      final newsletter = buildNewsletter(
        articles: [_article()],
        profileName: 'Tecnologia',
        summaryLabel: 'Resumo técnico',
        options: const NewsletterOptions(
          includeSummary: false,
          includeFullText: false,
        ),
        generatedAt: _generatedAt,
      );

      // O título e o link continuam; resumo e íntegra saem junto com seus rótulos.
      expect(newsletter.body, contains('1. Título do artigo'));
      expect(newsletter.body, contains('🔗 https://exemplo.com/artigo'));
      expect(newsletter.body, isNot(contains('Resumo curto.')));
      expect(newsletter.body, isNot(contains('Texto longo do artigo.')));
      expect(newsletter.body, isNot(contains('Íntegra')));
    });

    test('campo vazio não deixa um rótulo órfão no corpo', () {
      final newsletter = buildNewsletter(
        articles: [_article(summary: '   ', ttsText: '', sourceUrl: '')],
        profileName: 'Tecnologia',
        summaryLabel: 'Resumo técnico',
        generatedAt: _generatedAt,
      );

      expect(newsletter.body, contains('1. Título do artigo'));
      expect(newsletter.body, isNot(contains('Resumo técnico\n')));
      expect(newsletter.body, isNot(contains('Íntegra')));
      expect(newsletter.body, isNot(contains('🔗')));
    });

    test('sem artigos devolve um corpo explicativo e isEmpty', () {
      final newsletter = buildNewsletter(
        articles: const [],
        profileName: 'Tecnologia',
        generatedAt: _generatedAt,
      );

      expect(newsletter.isEmpty, isTrue);
      expect(newsletter.articleCount, 0);
      expect(newsletter.subject, contains('0 artigos não lidos'));
      expect(newsletter.body, contains('Nenhum artigo não lido na seleção atual do feed.'));
    });

    test('perfil vazio omite o nome do cabeçalho e do rodapé', () {
      final newsletter = buildNewsletter(
        articles: [_article()],
        generatedAt: _generatedAt,
      );

      expect(newsletter.subject, 'NewsFlow · 17/09/2026 (1 artigo não lido)');
      expect(newsletter.body, contains('📰 NewsFlow\n'));
      expect(newsletter.body, contains('Gerado pelo NewsFlow.'));
      expect(newsletter.body, isNot(contains('perfil "')));
    });
  });
}
