import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/profile.dart';
import '../providers/providers.dart';

/// Edita (ou cria) um perfil: nome, ícone, fontes e critérios de curadoria.
///
/// Ao salvar uma edição que muda fontes ou curadoria, pergunta o que fazer com
/// os artigos já curados sob os critérios antigos.
class ProfileEditScreen extends ConsumerStatefulWidget {
  const ProfileEditScreen({super.key, required this.profile, this.isNew = false});

  final Profile profile;
  final bool isNew;

  @override
  ConsumerState<ProfileEditScreen> createState() => _ProfileEditScreenState();
}

class _ProfileEditScreenState extends ConsumerState<ProfileEditScreen> {
  final _formKey = GlobalKey<FormState>();

  late TextEditingController _nameController;
  late TextEditingController _personaController;
  late TextEditingController _extraController;
  late TextEditingController _summaryLabelController;

  late String _icon;
  late int _minScore;
  late int _maxItems;
  late List<ProfileSource> _sources;
  late List<String> _approveCriteria;
  late List<String> _rejectCriteria;

  bool _saving = false;

  @override
  void initState() {
    super.initState();
    final p = widget.profile;
    _nameController = TextEditingController(text: p.name);
    _personaController = TextEditingController(text: p.curation.persona);
    _extraController = TextEditingController(text: p.curation.extraInstructions);
    _summaryLabelController = TextEditingController(text: p.curation.summaryLabel);
    _icon = p.icon;
    _minScore = p.curation.minScore;
    _maxItems = p.maxItemsPerRun;
    _sources = [...p.sources];
    _approveCriteria = [...p.curation.approveCriteria];
    _rejectCriteria = [...p.curation.rejectCriteria];
  }

  @override
  void dispose() {
    _nameController.dispose();
    _personaController.dispose();
    _extraController.dispose();
    _summaryLabelController.dispose();
    super.dispose();
  }

  /// Detecta se a mudança justifica perguntar sobre limpar artigos antigos.
  /// Renomear o perfil ou trocar o ícone não afeta a curadoria.
  bool get _curationChanged {
    final original = widget.profile;
    if (_sources.length != original.sources.length) return true;
    for (var i = 0; i < _sources.length; i++) {
      final a = _sources[i];
      final b = original.sources[i];
      if (a.type != b.type || a.url != b.url || a.name != b.name) return true;
    }
    final c = original.curation;
    return _personaController.text.trim() != c.persona.trim() ||
        _extraController.text.trim() != c.extraInstructions.trim() ||
        _minScore != c.minScore ||
        !_listEquals(_approveCriteria, c.approveCriteria) ||
        !_listEquals(_rejectCriteria, c.rejectCriteria);
  }

  bool _listEquals(List<String> a, List<String> b) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (a[i] != b[i]) return false;
    }
    return true;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.isNew ? 'Novo perfil' : 'Editar perfil'),
        actions: [
          TextButton(
            onPressed: _saving ? null : _save,
            child: _saving
                ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                : const Text('Salvar'),
          ),
        ],
      ),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            TextFormField(
              controller: _nameController,
              decoration: const InputDecoration(
                labelText: 'Nome do perfil',
                hintText: 'Ex: Política BR',
                border: OutlineInputBorder(),
              ),
              textCapitalization: TextCapitalization.sentences,
              validator: (value) =>
                  (value == null || value.trim().isEmpty) ? 'Dê um nome ao perfil' : null,
            ),
            const SizedBox(height: 20),
            Text('Ícone', style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              children: [
                for (final name in Profile.availableIcons)
                  ChoiceChip(
                    label: Icon(Profile(
                      id: '',
                      name: '',
                      icon: name,
                      active: false,
                      configVersion: 1,
                      sources: const [],
                      curation: const CurationConfig(),
                    ).iconData),
                    selected: _icon == name,
                    onSelected: (_) => setState(() => _icon = name),
                  ),
              ],
            ),
            const SizedBox(height: 24),
            _SectionHeader(
              title: 'Fontes',
              subtitle: '${_sources.length} configuradas',
              action: TextButton.icon(
                onPressed: _addSource,
                icon: const Icon(Icons.add, size: 18),
                label: const Text('Adicionar'),
              ),
            ),
            if (_sources.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 12),
                child: Text('Nenhuma fonte ainda. Adicione ao menos uma para o perfil funcionar.'),
              ),
            for (var i = 0; i < _sources.length; i++)
              Card(
                margin: const EdgeInsets.only(bottom: 8),
                child: ListTile(
                  dense: true,
                  leading: Icon(_sources[i].isRss ? Icons.rss_feed : Icons.api, size: 20),
                  title: Text(_sources[i].name),
                  subtitle: Text(_sources[i].subtitle, maxLines: 1, overflow: TextOverflow.ellipsis),
                  trailing: IconButton(
                    icon: const Icon(Icons.close, size: 20),
                    onPressed: () => setState(() => _sources.removeAt(i)),
                  ),
                ),
              ),
            const SizedBox(height: 24),
            _SectionHeader(title: 'Curadoria', subtitle: 'Como o Gemini filtra as notícias'),
            const SizedBox(height: 8),
            TextFormField(
              controller: _personaController,
              decoration: const InputDecoration(
                labelText: 'Persona do editor',
                hintText: 'Ex: Você é um editor de política sênior, factual e apartidário.',
                border: OutlineInputBorder(),
              ),
              maxLines: 4,
              textCapitalization: TextCapitalization.sentences,
            ),
            const SizedBox(height: 16),
            TextFormField(
              controller: _summaryLabelController,
              decoration: const InputDecoration(
                labelText: 'Rótulo do resumo',
                hintText: 'Ex: Resumo técnico',
                border: OutlineInputBorder(),
                helperText: 'Título da seção de resumo na tela do artigo.',
              ),
            ),
            const SizedBox(height: 20),
            Text('Score mínimo para aprovar: $_minScore', style: Theme.of(context).textTheme.titleSmall),
            Slider(
              value: _minScore.toDouble(),
              min: 40,
              max: 95,
              divisions: 11,
              label: '$_minScore',
              onChanged: (v) => setState(() => _minScore = v.round()),
            ),
            Text(
              'Quanto maior, mais rigorosa a curadoria (e menos artigos chegam).',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 20),
            _CriteriaEditor(
              title: 'Critérios de aprovação',
              hint: 'O que você quer receber',
              items: _approveCriteria,
              onChanged: (items) => setState(() => _approveCriteria = items),
            ),
            const SizedBox(height: 16),
            _CriteriaEditor(
              title: 'Critérios de rejeição',
              hint: 'O que você não quer receber',
              items: _rejectCriteria,
              onChanged: (items) => setState(() => _rejectCriteria = items),
            ),
            const SizedBox(height: 16),
            TextFormField(
              controller: _extraController,
              decoration: const InputDecoration(
                labelText: 'Instruções adicionais (opcional)',
                hintText: 'Ex: sempre inclua os números no resumo.',
                border: OutlineInputBorder(),
              ),
              maxLines: 3,
              textCapitalization: TextCapitalization.sentences,
            ),
            const SizedBox(height: 24),
            _SectionHeader(title: 'Avançado'),
            const SizedBox(height: 8),
            Text('Máximo de artigos por rodada: $_maxItems',
                style: Theme.of(context).textTheme.titleSmall),
            Slider(
              value: _maxItems.toDouble(),
              min: 10,
              max: 100,
              divisions: 9,
              label: '$_maxItems',
              onChanged: (v) => setState(() => _maxItems = v.round()),
            ),
            Text(
              'O pipeline roda a cada 30 minutos. Valores altos consomem mais rápido '
              'a cota gratuita do Gemini.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 32),
          ],
        ),
      ),
    );
  }

  Future<void> _addSource() async {
    final source = await showModalBottomSheet<ProfileSource>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (context) => const _AddSourceSheet(),
    );
    if (source != null) {
      setState(() => _sources.add(source));
    }
  }

  Future<void> _save() async {
    if (!(_formKey.currentState?.validate() ?? false)) return;

    if (_sources.isEmpty) {
      _showMessage('Adicione ao menos uma fonte antes de salvar.');
      return;
    }

    // Só pergunta sobre limpeza quando a mudança afeta o que será curado, e
    // apenas em edições — um perfil novo não tem artigos antigos.
    var cleanup = ProfileCleanupMode.keep;
    if (!widget.isNew && _curationChanged) {
      final choice = await _askCleanupMode();
      if (choice == null) return; // usuário cancelou o salvamento
      cleanup = choice;
    }

    setState(() => _saving = true);

    final service = ref.read(profileServiceProvider);
    final updated = widget.profile.copyWith(
      name: _nameController.text.trim(),
      icon: _icon,
      sources: _sources,
      maxItemsPerRun: _maxItems,
      curation: widget.profile.curation.copyWith(
        persona: _personaController.text.trim(),
        summaryLabel: _summaryLabelController.text.trim().isEmpty
            ? 'Resumo'
            : _summaryLabelController.text.trim(),
        minScore: _minScore,
        approveCriteria: _approveCriteria,
        rejectCriteria: _rejectCriteria,
        extraInstructions: _extraController.text.trim(),
      ),
    );

    try {
      if (widget.isNew) {
        await service.createProfile(updated);
      } else {
        await service.updateProfile(updated, cleanup: cleanup, bumpConfigVersion: _curationChanged);
      }
      if (!mounted) return;
      Navigator.of(context).pop();
      _showMessage(widget.isNew ? 'Perfil criado.' : 'Perfil salvo.');
    } catch (e) {
      if (!mounted) return;
      setState(() => _saving = false);
      _showMessage('Falha ao salvar: $e');
    }
  }

  /// Diálogo de 3 opções sobre o que fazer com os artigos já curados.
  /// Retorna `null` se o usuário cancelar (o salvamento é abortado).
  Future<ProfileCleanupMode?> _askCleanupMode() {
    return showDialog<ProfileCleanupMode>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('E os artigos atuais?'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Você mudou o que este perfil busca ou como ele filtra. Os artigos '
              'já no feed foram curados com os critérios antigos.',
            ),
            const SizedBox(height: 8),
            Text(
              'A limpeza roda no próximo ciclo do pipeline (até 30 min).',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancelar')),
          TextButton(
            onPressed: () => Navigator.pop(context, ProfileCleanupMode.keep),
            child: Text(ProfileCleanupMode.keep.label),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, ProfileCleanupMode.purgeAll),
            child: Text(ProfileCleanupMode.purgeAll.label),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, ProfileCleanupMode.purgeUnread),
            child: Text(ProfileCleanupMode.purgeUnread.label),
          ),
        ],
      ),
    );
  }

  void _showMessage(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }
}

class _SectionHeader extends StatelessWidget {
  const _SectionHeader({required this.title, this.subtitle, this.action});

  final String title;
  final String? subtitle;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(title, style: Theme.of(context).textTheme.titleMedium),
              if (subtitle != null)
                Text(subtitle!, style: Theme.of(context).textTheme.bodySmall),
            ],
          ),
        ),
        ?action,
      ],
    );
  }
}

/// Lista editável de critérios (uma frase por linha).
class _CriteriaEditor extends StatelessWidget {
  const _CriteriaEditor({
    required this.title,
    required this.hint,
    required this.items,
    required this.onChanged,
  });

  final String title;
  final String hint;
  final List<String> items;
  final ValueChanged<List<String>> onChanged;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title, style: Theme.of(context).textTheme.titleSmall),
                  Text(hint, style: Theme.of(context).textTheme.bodySmall),
                ],
              ),
            ),
            IconButton(
              icon: const Icon(Icons.add),
              onPressed: () => _addItem(context),
            ),
          ],
        ),
        for (var i = 0; i < items.length; i++)
          ListTile(
            dense: true,
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.circle, size: 8),
            title: Text(items[i], style: Theme.of(context).textTheme.bodyMedium),
            trailing: IconButton(
              icon: const Icon(Icons.close, size: 18),
              onPressed: () => onChanged([...items]..removeAt(i)),
            ),
            onTap: () => _addItem(context, editIndex: i),
          ),
      ],
    );
  }

  Future<void> _addItem(BuildContext context, {int? editIndex}) async {
    final controller = TextEditingController(text: editIndex != null ? items[editIndex] : '');
    final result = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(editIndex != null ? 'Editar critério' : 'Novo critério'),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLines: 3,
          textCapitalization: TextCapitalization.sentences,
          decoration: const InputDecoration(
            hintText: 'Descreva o critério em uma frase.',
            border: OutlineInputBorder(),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancelar')),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text.trim()),
            child: const Text('OK'),
          ),
        ],
      ),
    );
    controller.dispose();

    if (result == null || result.isEmpty) return;
    final updated = [...items];
    if (editIndex != null) {
      updated[editIndex] = result;
    } else {
      updated.add(result);
    }
    onChanged(updated);
  }
}

/// Adiciona uma fonte: RSS por URL (o caso comum) ou uma das fontes especiais.
class _AddSourceSheet extends StatefulWidget {
  const _AddSourceSheet();

  @override
  State<_AddSourceSheet> createState() => _AddSourceSheetState();
}

class _AddSourceSheetState extends State<_AddSourceSheet> {
  final _nameController = TextEditingController();
  final _urlController = TextEditingController();
  final _formKey = GlobalKey<FormState>();

  @override
  void dispose() {
    _nameController.dispose();
    _urlController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(
        left: 16,
        right: 16,
        bottom: MediaQuery.of(context).viewInsets.bottom + 16,
      ),
      child: Form(
        key: _formKey,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Adicionar fonte RSS', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 16),
            TextFormField(
              controller: _nameController,
              decoration: const InputDecoration(
                labelText: 'Nome',
                hintText: 'Ex: Folha - Poder',
                border: OutlineInputBorder(),
              ),
              textCapitalization: TextCapitalization.words,
              validator: (v) => (v == null || v.trim().isEmpty) ? 'Informe um nome' : null,
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _urlController,
              decoration: const InputDecoration(
                labelText: 'URL do feed',
                hintText: 'https://exemplo.com/feed/',
                border: OutlineInputBorder(),
              ),
              keyboardType: TextInputType.url,
              autocorrect: false,
              validator: (v) {
                final value = v?.trim() ?? '';
                if (value.isEmpty) return 'Informe a URL do feed';
                final uri = Uri.tryParse(value);
                if (uri == null || !uri.isScheme('http') && !uri.isScheme('https')) {
                  return 'URL inválida (precisa começar com http:// ou https://)';
                }
                return null;
              },
            ),
            const SizedBox(height: 8),
            Text(
              'Aceita RSS, Atom e RDF. Procure por "RSS" ou "feed" no site desejado.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 16),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                TextButton(
                  onPressed: () => Navigator.pop(context),
                  child: const Text('Cancelar'),
                ),
                const SizedBox(width: 8),
                FilledButton(
                  onPressed: () {
                    if (!(_formKey.currentState?.validate() ?? false)) return;
                    Navigator.pop(
                      context,
                      ProfileSource(
                        type: 'rss',
                        name: _nameController.text.trim(),
                        url: _urlController.text.trim(),
                        limit: 20,
                      ),
                    );
                  },
                  child: const Text('Adicionar'),
                ),
              ],
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }
}
