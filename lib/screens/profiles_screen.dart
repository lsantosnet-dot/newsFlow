import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/profile.dart';
import '../providers/providers.dart';
import '../services/profile_service.dart';
import 'profile_edit_screen.dart';

/// Lista os perfis de curadoria: ativar, criar, duplicar, editar e apagar.
///
/// Apenas um perfil fica ativo por vez — é o que o pipeline cura e o que o feed
/// exibe. Os artigos dos perfis inativos continuam no Firestore.
class ProfilesScreen extends ConsumerWidget {
  const ProfilesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profilesAsync = ref.watch(profilesProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Perfis de curadoria')),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => _createProfile(context, ref),
        icon: const Icon(Icons.add),
        label: const Text('Novo perfil'),
      ),
      body: profilesAsync.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) => _ErrorState(message: error.toString()),
        data: (profiles) {
          if (profiles.isEmpty) {
            return const _EmptyState();
          }

          final showLimitWarning = profiles.length >= ProfileService.softProfileLimit;

          return ListView(
            padding: const EdgeInsets.only(bottom: 88),
            children: [
              if (showLimitWarning) _ProfileLimitBanner(count: profiles.length),
              for (final profile in profiles)
                _ProfileTile(
                  profile: profile,
                  canDelete: profiles.length > 1 && !profile.active,
                ),
            ],
          );
        },
      ),
    );
  }

  Future<void> _createProfile(BuildContext context, WidgetRef ref) async {
    final service = ref.read(profileServiceProvider);

    final List<Profile> presets;
    try {
      presets = await service.loadPresets();
    } catch (e) {
      if (context.mounted) _showError(context, 'Falha ao carregar os templates: $e');
      return;
    }

    if (!context.mounted) return;

    final existing = ref.read(profilesProvider).valueOrNull ?? const <Profile>[];
    final template = await showModalBottomSheet<Profile>(
      context: context,
      showDragHandle: true,
      builder: (context) => _TemplatePicker(presets: presets, existing: existing),
    );

    if (template == null || !context.mounted) return;

    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => ProfileEditScreen(profile: template, isNew: true),
      ),
    );
  }

  static void _showError(BuildContext context, String message) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }
}

/// Aviso suave quando a lista de perfis fica grande. Não bloqueia nada.
class _ProfileLimitBanner extends StatelessWidget {
  const _ProfileLimitBanner({required this.count});

  final int count;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      margin: const EdgeInsets.fromLTRB(12, 12, 12, 4),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: scheme.tertiaryContainer.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        children: [
          Icon(Icons.info_outline, size: 20, color: scheme.onTertiaryContainer),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              'Você tem $count perfis. Considere apagar os que não usa mais — '
              'artigos de perfis inativos são limpos automaticamente, mas a lista '
              'fica mais fácil de navegar com poucos perfis.',
              style: Theme.of(context).textTheme.bodySmall?.copyWith(color: scheme.onTertiaryContainer),
            ),
          ),
        ],
      ),
    );
  }
}

class _ProfileTile extends ConsumerWidget {
  const _ProfileTile({required this.profile, required this.canDelete});

  final Profile profile;
  final bool canDelete;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final scheme = Theme.of(context).colorScheme;
    final sourceCount = profile.sources.length;

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      color: profile.active ? scheme.primaryContainer.withValues(alpha: 0.35) : null,
      child: ListTile(
        leading: CircleAvatar(
          backgroundColor: profile.active ? scheme.primary : scheme.surfaceContainerHighest,
          child: Icon(
            profile.iconData,
            color: profile.active ? scheme.onPrimary : scheme.onSurfaceVariant,
          ),
        ),
        title: Row(
          children: [
            Flexible(child: Text(profile.name, overflow: TextOverflow.ellipsis)),
            if (profile.active) ...[
              const SizedBox(width: 8),
              Chip(
                label: const Text('Ativo'),
                labelStyle: TextStyle(fontSize: 11, color: scheme.onPrimary),
                backgroundColor: scheme.primary,
                visualDensity: VisualDensity.compact,
                side: BorderSide.none,
                padding: EdgeInsets.zero,
              ),
            ],
          ],
        ),
        subtitle: Text(
          '$sourceCount ${sourceCount == 1 ? 'fonte' : 'fontes'}'
          '${profile.hasPendingCleanup ? ' • limpeza pendente' : ''}',
        ),
        trailing: PopupMenuButton<String>(
          onSelected: (action) => _handleAction(context, ref, action),
          itemBuilder: (context) => [
            if (!profile.active) const PopupMenuItem(value: 'activate', child: Text('Ativar')),
            const PopupMenuItem(value: 'edit', child: Text('Editar')),
            const PopupMenuItem(value: 'duplicate', child: Text('Duplicar')),
            if (canDelete)
              const PopupMenuItem(
                value: 'delete',
                child: Text('Apagar', style: TextStyle(color: Colors.redAccent)),
              ),
          ],
        ),
        onTap: () => _openEdit(context),
      ),
    );
  }

  void _openEdit(BuildContext context) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => ProfileEditScreen(profile: profile)),
    );
  }

  Future<void> _handleAction(BuildContext context, WidgetRef ref, String action) async {
    final service = ref.read(profileServiceProvider);
    final messenger = ScaffoldMessenger.of(context);

    switch (action) {
      case 'activate':
        try {
          await service.activateProfile(profile.id);
          messenger.showSnackBar(SnackBar(content: Text('Perfil "${profile.name}" ativado.')));
        } catch (e) {
          messenger.showSnackBar(SnackBar(content: Text('Falha ao ativar: $e')));
        }
      case 'edit':
        _openEdit(context);
      case 'duplicate':
        final copy = profile.copyWith(name: '${profile.name} (cópia)', active: false);
        if (!context.mounted) return;
        await Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) => ProfileEditScreen(profile: copy, isNew: true),
          ),
        );
      case 'delete':
        final confirmed = await _confirmDelete(context);
        if (confirmed != true) return;
        try {
          await service.deleteProfile(profile.id);
          messenger.showSnackBar(SnackBar(content: Text('Perfil "${profile.name}" apagado.')));
        } catch (e) {
          messenger.showSnackBar(SnackBar(content: Text(_friendlyError(e))));
        }
    }
  }

  Future<bool?> _confirmDelete(BuildContext context) {
    return showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Apagar "${profile.name}"?'),
        content: const Text(
          'O perfil e sua configuração são removidos. Os artigos já curados por '
          'ele são limpos automaticamente pelo pipeline (favoritos são preservados).',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancelar')),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            style: FilledButton.styleFrom(backgroundColor: Colors.redAccent),
            child: const Text('Apagar'),
          ),
        ],
      ),
    );
  }

  String _friendlyError(Object error) {
    if (error is StateError) return error.message;
    return 'Falha ao apagar: $error';
  }
}

/// Escolha do template ao criar um perfil novo — evita montar fontes e
/// critérios do zero no celular.
class _TemplatePicker extends StatelessWidget {
  const _TemplatePicker({required this.presets, required this.existing});

  final List<Profile> presets;
  final List<Profile> existing;

  @override
  Widget build(BuildContext context) {
    final existingNames = existing.map((p) => p.name.toLowerCase()).toSet();

    return SafeArea(
      child: ListView(
        shrinkWrap: true,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: Text('Começar a partir de', style: Theme.of(context).textTheme.titleMedium),
          ),
          for (final preset in presets)
            ListTile(
              leading: Icon(preset.iconData),
              title: Text(preset.name),
              subtitle: Text(
                '${preset.sources.length} fontes'
                '${existingNames.contains(preset.name.toLowerCase()) ? ' • você já tem um perfil com este nome' : ''}',
              ),
              onTap: () => Navigator.pop(
                context,
                // Um template nunca nasce com o id do preset: o Firestore gera
                // um id novo, para não sobrescrever um perfil existente.
                preset.copyWith(id: '', active: false, configVersion: 1),
              ),
            ),
          const Divider(),
          ListTile(
            leading: const Icon(Icons.note_add_outlined),
            title: const Text('Perfil em branco'),
            subtitle: const Text('Configurar fontes e critérios do zero'),
            onTap: () => Navigator.pop(
              context,
              const Profile(
                id: '',
                name: '',
                icon: 'newspaper',
                active: false,
                configVersion: 1,
                sources: [],
                curation: CurationConfig(),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState();

  @override
  Widget build(BuildContext context) {
    return const Center(
      child: Padding(
        padding: EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.tune, size: 48),
            SizedBox(height: 16),
            Text(
              'Nenhum perfil ainda.\nCrie o primeiro para começar a receber notícias.',
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }
}

class _ErrorState extends StatelessWidget {
  const _ErrorState({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.error_outline, size: 48),
            const SizedBox(height: 16),
            Text('Falha ao carregar os perfis.\n$message', textAlign: TextAlign.center),
          ],
        ),
      ),
    );
  }
}
