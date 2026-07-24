// PLACEHOLDER — este arquivo deve ser gerado (e sobrescrito) pelo comando:
//   flutterfire configure
// Veja o README.md na raiz do projeto para o passo a passo completo de
// criação do projeto Firebase (plano Spark) e geração deste arquivo.
//
// Os valores abaixo são fictícios e não conectam a nenhum projeto real.
// O app não deve ser rodado antes de este arquivo ser regenerado.

import 'package:firebase_core/firebase_core.dart' show FirebaseOptions;
import 'package:flutter/foundation.dart' show TargetPlatform, defaultTargetPlatform, kIsWeb;

class DefaultFirebaseOptions {
  static FirebaseOptions get currentPlatform {
    if (kIsWeb) {
      throw UnsupportedError(
        'DefaultFirebaseOptions não configura a Web. Rode `flutterfire configure` para gerar as opções corretas.',
      );
    }
    switch (defaultTargetPlatform) {
      case TargetPlatform.android:
        return android;
      default:
        throw UnsupportedError(
          'DefaultFirebaseOptions só está configurado para Android neste projeto '
          '(app de uso pessoal). Rode `flutterfire configure` para adicionar outras plataformas.',
        );
    }
  }

  static const FirebaseOptions android = FirebaseOptions(
    apiKey: 'PLACEHOLDER_API_KEY',
    appId: 'PLACEHOLDER_APP_ID',
    messagingSenderId: 'PLACEHOLDER_SENDER_ID',
    projectId: 'PLACEHOLDER_PROJECT_ID',
    storageBucket: 'PLACEHOLDER_PROJECT_ID.appspot.com',
  );
}
