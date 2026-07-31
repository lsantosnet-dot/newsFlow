import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../providers/providers.dart';

/// Mostra "X não lidos de Y" para um perfil, contando direto no servidor.
///
/// Usado no seletor de perfil do feed e na tela de perfis, para você ver quanto
/// acumulou em cada perfil sem precisar ativá-lo.
class ProfileCountsLabel extends ConsumerWidget {
  const ProfileCountsLabel({
    super.key,
    required this.profileId,
    this.sourceCount,
    this.style,
  });

  final String profileId;

  /// Quando informado, prefixa a contagem com "N fontes • ".
  final int? sourceCount;

  final TextStyle? style;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final countsAsync = ref.watch(profileCountsProvider(profileId));
    final prefix = sourceCount != null
        ? '$sourceCount ${sourceCount == 1 ? 'fonte' : 'fontes'} • '
        : '';

    final text = countsAsync.when(
      loading: () => '${prefix}contando...',
      // Uma falha de contagem não deve poluir a UI: o resto da tela funciona.
      error: (_, _) => prefix.isEmpty ? '' : prefix.replaceAll(' • ', ''),
      data: (counts) => '$prefix${counts.label}',
    );

    if (text.isEmpty) return const SizedBox.shrink();

    return Text(
      text,
      style: style ?? Theme.of(context).textTheme.bodySmall,
      overflow: TextOverflow.ellipsis,
    );
  }
}

/// Badge com o número de não lidos, para destacar perfis com conteúdo novo.
/// Fica oculto quando não há nada não lido.
class UnreadBadge extends ConsumerWidget {
  const UnreadBadge({super.key, required this.profileId});

  final String profileId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final counts = ref.watch(profileCountsProvider(profileId)).valueOrNull;
    if (counts == null || counts.unread == 0) return const SizedBox.shrink();

    final scheme = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: scheme.primary,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Text(
        '${counts.unread}',
        style: TextStyle(
          color: scheme.onPrimary,
          fontSize: 11,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}
