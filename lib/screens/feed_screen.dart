import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/profile.dart';
import '../providers/providers.dart';
import '../widgets/article_card.dart';
import '../widgets/profile_counts_label.dart';
import 'article_detail_screen.dart';
import 'profiles_screen.dart';
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

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    // Semeia os presets no primeiro launch (idempotente).
    ref.watch(seedPresetsProvider);

    // Trocar de perfil zera os filtros (tags do perfil anterior não existem
    // aqui) e para o modo podcast, que estaria lendo a fila antiga.
    ref.listen<String?>(activeProfileIdProvider, (previous, next) {
      if (previous == null || previous == next) return;
      ref.read(selectedTagProvider.notifier).state = null;
      ref.read(readFilterProvider.notifier).state = ReadFilterOption.all;
      ref.read(favoritesOnlyProvider.notifier).state = false;
      ref.read(podcastProvider.notifier).stop();
    });

    final activeProfile = ref.watch(activeProfileProvider).valueOrNull;
    final feedState = ref.watch(articleFeedProvider);
    final selectedTag = ref.watch(selectedTagProvider);
    final sortOrder = ref.watch(articleSortOrderProvider);
    final readFilter = ref.watch(readFilterProvider);
    final favoritesOnly = ref.watch(favoritesOnlyProvider);
    final unreadCount = ref.watch(unreadCountProvider).asData?.value;
    final filtered = ref.watch(filteredArticlesProvider);
    final podcastState = ref.watch(podcastProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('NewsFlow'),
        titleSpacing: 0,
        actions: [
          IconButton(
            icon: Badge(
              label: Text('${unreadCount ?? 0}'),
              isLabelVisible: (unreadCount ?? 0) > 0,
              child: const Icon(Icons.mark_email_unread_outlined),
            ),
            tooltip: unreadCount == null
                ? 'Contando não lidos...'
                : '$unreadCount artigo(s) não lido(s) no Firestore',
            onPressed: () => ref.read(readFilterProvider.notifier).state = ReadFilterOption.unread,
          ),
          IconButton(
            icon: Icon(favoritesOnly ? Icons.star : Icons.star_border),
            color: favoritesOnly ? theme.colorScheme.tertiary : null,
            tooltip: favoritesOnly ? 'Mostrar todos os artigos' : 'Mostrar somente favoritos',
            onPressed: () => ref.read(favoritesOnlyProvider.notifier).state = !favoritesOnly,
          ),
          IconButton(
            icon: Icon(
              podcastState.isPlaying
                  ? Icons.pause_circle_filled
                  : podcastState.isPaused
                      ? Icons.play_circle_fill
                      : Icons.podcasts,
            ),
            tooltip: podcastState.isPlaying
                ? 'Pausar modo podcast'
                : podcastState.isPaused
                    ? 'Retomar modo podcast'
                    : 'Modo podcast: ouvir os artigos em sequência',
            onPressed: () {
              if (podcastState.isPlaying) {
                ref.read(podcastProvider.notifier).pause();
              } else if (podcastState.isPaused) {
                ref.read(podcastProvider.notifier).resume();
              } else {
                ref.read(podcastProvider.notifier).start();
              }
            },
          ),
          IconButton(
            icon: Icon(sortOrder == ArticleSortOrder.dateDesc ? Icons.arrow_downward : Icons.arrow_upward),
            tooltip: sortOrder == ArticleSortOrder.dateDesc
                ? 'Ordenar por data: mais recentes primeiro'
                : 'Ordenar por data: mais antigos primeiro',
            onPressed: () {
              ref.read(articleSortOrderProvider.notifier).state = sortOrder == ArticleSortOrder.dateDesc
                  ? ArticleSortOrder.dateAsc
                  : ArticleSortOrder.dateDesc;
            },
          ),
          PopupMenuButton<ReadFilterOption>(
            icon: const Icon(Icons.filter_list),
            tooltip: 'Filtrar por status de leitura',
            initialValue: readFilter,
            onSelected: (value) => ref.read(readFilterProvider.notifier).state = value,
            itemBuilder: (context) => [
              CheckedPopupMenuItem(
                value: ReadFilterOption.all,
                checked: readFilter == ReadFilterOption.all,
                child: const Text('Todos'),
              ),
              CheckedPopupMenuItem(
                value: ReadFilterOption.unread,
                checked: readFilter == ReadFilterOption.unread,
                child: const Text('Não lidos'),
              ),
              CheckedPopupMenuItem(
                value: ReadFilterOption.read,
                checked: readFilter == ReadFilterOption.read,
                child: const Text('Lidos'),
              ),
            ],
          ),
          PopupMenuButton<String>(
            icon: const Icon(Icons.more_vert),
            onSelected: (value) {
              final route = value == 'profiles'
                  ? MaterialPageRoute<void>(builder: (_) => const ProfilesScreen())
                  : MaterialPageRoute<void>(builder: (_) => const SettingsScreen());
              Navigator.of(context).push(route);
            },
            itemBuilder: (context) => const [
              PopupMenuItem(
                value: 'profiles',
                child: ListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  leading: Icon(Icons.tune),
                  title: Text('Perfis de curadoria'),
                ),
              ),
              PopupMenuItem(
                value: 'settings',
                child: ListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  leading: Icon(Icons.settings),
                  title: Text('Configurações'),
                ),
              ),
            ],
          ),
        ],
      ),
      body: Column(
        children: [
          _ProfileTitle(profile: activeProfile),
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
                          children: [
                            const SizedBox(height: 100),
                            // Sem isto, uma falha de query (índice faltando,
                            // permissão negada) fica indistinguível de "não há
                            // artigos" — e o erro real nunca chega à tela.
                            if (feedState.error != null)
                              Padding(
                                padding: const EdgeInsets.symmetric(horizontal: 24),
                                child: Column(
                                  children: [
                                    Icon(Icons.error_outline,
                                        size: 40, color: theme.colorScheme.error),
                                    const SizedBox(height: 12),
                                    Text('Falha ao carregar o feed',
                                        style: theme.textTheme.titleMedium),
                                    const SizedBox(height: 8),
                                    Text(
                                      feedState.error!,
                                      textAlign: TextAlign.center,
                                      style: theme.textTheme.bodySmall,
                                    ),
                                    const SizedBox(height: 16),
                                    FilledButton.icon(
                                      onPressed: () =>
                                          ref.read(articleFeedProvider.notifier).refresh(),
                                      icon: const Icon(Icons.refresh),
                                      label: const Text('Tentar de novo'),
                                    ),
                                  ],
                                ),
                              )
                            else
                              const Center(child: Text('Nenhum artigo curado ainda.')),
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
          if (podcastState.isActive)
            _PodcastBar(currentArticleId: podcastState.currentArticleId, isPlaying: podcastState.isPlaying),
        ],
      ),
    );
  }
}

/// Barra do perfil ativo, logo abaixo da AppBar: mostra qual perfil está em uso
/// e abre o seletor rápido ao tocar. Fica no corpo (e não como título da
/// AppBar) porque a AppBar já tem 6 ações e não sobra largura para o nome.
///
/// Trocar de perfil só muda o que o feed exibe — nenhum artigo é apagado.
class _ProfileTitle extends ConsumerWidget {
  const _ProfileTitle({required this.profile});

  final Profile? profile;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    if (profile == null) return const SizedBox.shrink();

    final theme = Theme.of(context);
    return Material(
      color: theme.colorScheme.surfaceContainerHighest,
      child: InkWell(
        onTap: () => _showProfilePicker(context, ref),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          child: Row(
            children: [
              Icon(profile!.iconData, size: 18, color: theme.colorScheme.primary),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  profile!.name,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.titleSmall,
                ),
              ),
              Text('trocar', style: theme.textTheme.labelSmall),
              const Icon(Icons.arrow_drop_down, size: 20),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _showProfilePicker(BuildContext context, WidgetRef ref) async {
    final profiles = ref.read(profilesProvider).valueOrNull ?? const <Profile>[];
    if (profiles.isEmpty) return;

    final selectedId = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: ListView(
          shrinkWrap: true,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Text('Trocar de perfil', style: Theme.of(context).textTheme.titleMedium),
            ),
            for (final item in profiles)
              ListTile(
                leading: Icon(item.iconData),
                title: Text(item.name),
                subtitle: ProfileCountsLabel(
                  profileId: item.id,
                  sourceCount: item.sources.length,
                ),
                trailing: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    UnreadBadge(profileId: item.id),
                    if (item.active) ...[
                      const SizedBox(width: 8),
                      const Icon(Icons.check, color: Colors.green),
                    ],
                  ],
                ),
                onTap: item.active ? null : () => Navigator.pop(context, item.id),
              ),
            const Divider(),
            ListTile(
              leading: const Icon(Icons.tune),
              title: const Text('Gerenciar perfis'),
              onTap: () {
                Navigator.pop(context);
                Navigator.of(context).push(
                  MaterialPageRoute<void>(builder: (_) => const ProfilesScreen()),
                );
              },
            ),
          ],
        ),
      ),
    );

    if (selectedId == null || !context.mounted) return;

    final messenger = ScaffoldMessenger.of(context);
    try {
      await ref.read(profileServiceProvider).activateProfile(selectedId);
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('Falha ao trocar de perfil: $e')));
    }
  }
}

class _PodcastBar extends ConsumerWidget {
  const _PodcastBar({required this.currentArticleId, required this.isPlaying});

  final String? currentArticleId;
  final bool isPlaying;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final ttsService = ref.watch(ttsServiceProvider);
    final feedState = ref.watch(articleFeedProvider);
    final matches = feedState.articles.where((a) => a.id == currentArticleId);
    final title = matches.isEmpty ? 'Carregando próximo artigo...' : matches.first.title;

    return Material(
      elevation: 8,
      color: theme.colorScheme.surfaceContainerHighest,
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 8, 8),
          child: Row(
            children: [
              Icon(Icons.podcasts, color: theme.colorScheme.primary),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 4),
                    StreamBuilder<double>(
                      stream: ttsService.progressStream,
                      initialData: 0.0,
                      builder: (context, snapshot) {
                        return LinearProgressIndicator(value: snapshot.data ?? 0.0);
                      },
                    ),
                  ],
                ),
              ),
              IconButton(
                icon: Icon(isPlaying ? Icons.pause_circle_filled : Icons.play_circle_fill),
                tooltip: isPlaying ? 'Pausar' : 'Retomar',
                onPressed: () {
                  final notifier = ref.read(podcastProvider.notifier);
                  isPlaying ? notifier.pause() : notifier.resume();
                },
              ),
              IconButton(
                icon: const Icon(Icons.stop_circle),
                tooltip: 'Parar modo podcast',
                onPressed: () => ref.read(podcastProvider.notifier).stop(),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
