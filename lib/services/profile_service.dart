import 'dart:convert';

import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:flutter/services.dart' show rootBundle;

import '../models/profile.dart';

/// Encapsula o acesso à coleção `profiles` do Firestore.
///
/// Invariante central: exatamente um perfil tem `active == true`. É esse perfil
/// que o pipeline Python roda e que o feed exibe.
class ProfileService {
  ProfileService({FirebaseFirestore? firestore}) : _firestore = firestore ?? FirebaseFirestore.instance;

  final FirebaseFirestore _firestore;

  /// A partir de quantos perfis a UI mostra um aviso de que a lista está grande.
  static const int softProfileLimit = 10;

  static const String _presetsAsset = 'assets/presets/profiles.json';

  CollectionReference<Map<String, dynamic>> get _profiles => _firestore.collection('profiles');

  Stream<List<Profile>> watchProfiles() {
    return _profiles.snapshots().map(
          (snapshot) => snapshot.docs.map(Profile.fromFirestore).toList()..sort((a, b) => a.name.compareTo(b.name)),
        );
  }

  Stream<Profile?> watchActiveProfile() {
    return _profiles.where('active', isEqualTo: true).limit(1).snapshots().map(
          (snapshot) => snapshot.docs.isEmpty ? null : Profile.fromFirestore(snapshot.docs.first),
        );
  }

  Future<List<Profile>> fetchProfiles() async {
    final snapshot = await _profiles.get();
    return snapshot.docs.map(Profile.fromFirestore).toList()..sort((a, b) => a.name.compareTo(b.name));
  }

  /// Lê os presets do bundle. Usados no seed inicial e como templates ao criar
  /// um perfil novo.
  Future<List<Profile>> loadPresets() async {
    final raw = await rootBundle.loadString(_presetsAsset);
    final decoded = jsonDecode(raw) as Map<String, dynamic>;
    final presets = (decoded['presets'] as List<dynamic>).cast<Map<String, dynamic>>();
    return presets.map((preset) => Profile.fromMap(preset['id'] as String, preset)).toList();
  }

  /// Semeia os presets no primeiro launch, se a coleção estiver vazia.
  ///
  /// Idempotente: se já houver qualquer perfil, não faz nada — assim o app não
  /// recria perfis que o usuário apagou de propósito.
  Future<void> seedPresetsIfEmpty() async {
    final existing = await _profiles.limit(1).get();
    if (existing.docs.isNotEmpty) return;

    final presets = await loadPresets();
    final batch = _firestore.batch();
    for (final preset in presets) {
      batch.set(_profiles.doc(preset.id), preset.toMap());
    }
    await batch.commit();
  }

  /// Ativa um perfil e desativa todos os outros, atomicamente.
  ///
  /// Precisa ser transação: dois `update` soltos podem deixar zero ou dois
  /// perfis ativos se algo falhar no meio, e o pipeline não saberia qual rodar.
  Future<void> activateProfile(String profileId) async {
    await _firestore.runTransaction((transaction) async {
      final snapshot = await _profiles.where('active', isEqualTo: true).get();

      // Leituras antes das escritas — exigência do Firestore em transações.
      final target = await transaction.get(_profiles.doc(profileId));
      if (!target.exists) {
        throw StateError('Perfil $profileId não existe.');
      }

      for (final doc in snapshot.docs) {
        if (doc.id != profileId) {
          transaction.update(doc.reference, {'active': false});
        }
      }
      transaction.update(_profiles.doc(profileId), {'active': true});
    });
  }

  Future<String> createProfile(Profile profile) async {
    final doc = _profiles.doc();
    // Um perfil novo nunca nasce ativo: a troca é sempre explícita, via
    // activateProfile, para preservar a invariante de um único ativo.
    await doc.set(profile.copyWith(active: false, configVersion: 1).toMap());
    return doc.id;
  }

  /// Salva a edição de um perfil e sinaliza o purge escolhido pelo usuário.
  ///
  /// `config_version` é incrementado quando a curadoria muda, para distinguir
  /// artigos curados com os critérios antigos dos novos.
  Future<void> updateProfile(
    Profile profile, {
    ProfileCleanupMode cleanup = ProfileCleanupMode.keep,
    bool bumpConfigVersion = true,
  }) async {
    final data = profile.toMap();
    data['pending_cleanup'] = cleanup.firestoreValue;
    if (bumpConfigVersion) {
      data['config_version'] = profile.configVersion + 1;
    }
    // O estado `active` é gerenciado só por activateProfile.
    data.remove('active');
    await _profiles.doc(profile.id).update(data);
  }

  /// Apaga um perfil.
  ///
  /// Recusa apagar o último perfil restante — ficar com zero perfis deixa o app
  /// vazio e o pipeline sem trabalho. Trocar o perfil ativo antes de apagar é
  /// responsabilidade da UI.
  Future<void> deleteProfile(String profileId) async {
    final all = await _profiles.get();
    if (all.docs.length <= 1) {
      throw StateError('Não é possível apagar o último perfil.');
    }

    final target = all.docs.where((doc) => doc.id == profileId).firstOrNull;
    if (target != null && (target.data()['active'] as bool? ?? false)) {
      throw StateError('Ative outro perfil antes de apagar este.');
    }

    await _profiles.doc(profileId).delete();
  }
}
