import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:share_plus/share_plus.dart';

import '../models/article.dart';
import '../providers/providers.dart';
import '../services/newsletter_builder.dart';

/// Monta um email no formato de newsletter com os artigos não lidos que o feed
/// já carregou, mostra a prévia e entrega o texto ao app de email.
///
/// A prévia existe (em vez de abrir o share sheet direto do menu) porque o
/// texto pode passar de dezenas de milhares de caracteres: dá para conferir o
/// tamanho e desligar a íntegra antes de enviar — e, se o app de email
/// escolhido se comportar mal, o botão Copiar continua disponível.
class NewsletterScreen extends ConsumerStatefulWidget {
  const NewsletterScreen({super.key});

  @override
  ConsumerState<NewsletterScreen> createState() => _NewsletterScreenState();
}

class _NewsletterScreenState extends ConsumerState<NewsletterScreen> {
  /// Fotografia dos artigos no instante em que a tela abriu.
  ///
  /// Não é um `watch`: marcar os artigos como lidos ao compartilhar esvaziaria
  /// `newsletterArticlesProvider` e a prévia sumiria debaixo do próprio envio.
  /// Uma newsletter é uma edição fechada — o conteúdo não muda depois de aberta.
  late final List<Article> _articles;
  late final DateTime _generatedAt;

  NewsletterOptions _options = const NewsletterOptions();
  bool _markAsRead = false;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _articles = ref.read(newsletterArticlesProvider);
    _generatedAt = DateTime.now();
  }

  /// Aplica o toggle "marcar como lidos". Só é chamado depois de o texto ter
  /// efetivamente saído da tela (copiado ou compartilhado sem cancelar).
  Future<void> _applyMarkAsRead(ScaffoldMessengerState messenger) async {
    if (!_markAsRead) return;
    try {
      await ref.read(articleFeedProvider.notifier).markManyAsRead(
            [for (final article in _articles) article.id],
          );
      messenger.showSnackBar(
        SnackBar(content: Text('${_articles.length} artigo(s) marcado(s) como lido(s).')),
      );
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('Falha ao marcar como lidos: $e')));
    }
  }

  Future<void> _copy(Newsletter newsletter) async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _busy = true);
    try {
      await Clipboard.setData(ClipboardData(text: newsletter.body));
      messenger.showSnackBar(
        const SnackBar(content: Text('Newsletter copiada para a área de transferência.')),
      );
      await _applyMarkAsRead(messenger);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _share(Newsletter newsletter) async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _busy = true);
    try {
      final result = await SharePlus.instance.share(
        ShareParams(text: newsletter.body, subject: newsletter.subject),
      );
      // Só `dismissed` bloqueia o "marcar como lidos": no Android o status
      // volta `unavailable` em envios legítimos sempre que a plataforma não
      // informa qual app recebeu o texto, e tratar isso como cancelamento
      // deixaria a newsletter enviada sem nunca fechar os artigos.
      if (result.status != ShareResultStatus.dismissed) {
        await _applyMarkAsRead(messenger);
      }
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('Falha ao compartilhar: $e')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final profile = ref.watch(activeProfileProvider).valueOrNull;
    final summaryLabel = profile?.curation.summaryLabel ?? 'Resumo';

    final newsletter = buildNewsletter(
      articles: _articles,
      profileName: profile?.name ?? '',
      summaryLabel: summaryLabel,
      options: _options,
      generatedAt: _generatedAt,
    );

    return Scaffold(
      appBar: AppBar(title: const Text('Newsletter')),
      body: newsletter.isEmpty
          ? const _EmptyState()
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  '${_articles.length} artigo(s) não lido(s) já carregado(s) no feed, '
                  'respeitando a tag e os filtros ativos.',
                  style: theme.textTheme.bodySmall,
                ),
                const SizedBox(height: 12),
                Card(
                  child: Column(
                    children: [
                      SwitchListTile(
                        title: Text(summaryLabel),
                        subtitle: const Text('O TL;DR gerado na curadoria.'),
                        value: _options.includeSummary,
                        onChanged: (value) => setState(
                          () => _options = _options.copyWith(includeSummary: value),
                        ),
                      ),
                      SwitchListTile(
                        title: const Text('Íntegra'),
                        subtitle: const Text(
                          'Texto longo do artigo, o mesmo usado na leitura em voz alta.',
                        ),
                        value: _options.includeFullText,
                        onChanged: (value) => setState(
                          () => _options = _options.copyWith(includeFullText: value),
                        ),
                      ),
                      SwitchListTile(
                        title: const Text('Link do artigo'),
                        subtitle: const Text('Endereço da fonte original.'),
                        value: _options.includeLink,
                        onChanged: (value) => setState(
                          () => _options = _options.copyWith(includeLink: value),
                        ),
                      ),
                      const Divider(height: 1),
                      SwitchListTile(
                        title: const Text('Marcar como lidos ao enviar'),
                        subtitle: const Text(
                          'Aplicado depois do envio; não acontece se você cancelar o compartilhamento.',
                        ),
                        value: _markAsRead,
                        onChanged: (value) => setState(() => _markAsRead = value),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 20),
                Row(
                  children: [
                    Text('Prévia', style: theme.textTheme.titleSmall),
                    const Spacer(),
                    Text(
                      '${newsletter.body.length} caracteres',
                      style: theme.textTheme.labelSmall,
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Card(
                  color: theme.colorScheme.surfaceContainerHighest,
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: SelectableText(
                      newsletter.body,
                      style: theme.textTheme.bodySmall,
                    ),
                  ),
                ),
                const SizedBox(height: 16),
              ],
            ),
      bottomNavigationBar: newsletter.isEmpty
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
                child: Row(
                  children: [
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: _busy ? null : () => _copy(newsletter),
                        icon: const Icon(Icons.content_copy),
                        label: const Text('Copiar'),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: FilledButton.icon(
                        onPressed: _busy ? null : () => _share(newsletter),
                        icon: const Icon(Icons.share),
                        label: const Text('Compartilhar'),
                      ),
                    ),
                  ],
                ),
              ),
            ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.mark_email_read_outlined, size: 48, color: theme.colorScheme.outline),
            const SizedBox(height: 16),
            Text('Nenhum artigo não lido', style: theme.textTheme.titleMedium),
            const SizedBox(height: 8),
            Text(
              'A newsletter usa os artigos não lidos que o feed já carregou. '
              'Role o feed para carregar mais páginas ou afrouxe os filtros.',
              textAlign: TextAlign.center,
              style: theme.textTheme.bodySmall,
            ),
          ],
        ),
      ),
    );
  }
}
