# NewsFlow — App Pessoal de Notícias Tech

App de uso **pessoal e exclusivo** (Flutter + Firestore + TTS nativo) para acompanhar
notícias de engenharia de software e IA, curadas automaticamente por um pipeline
Python que roda de graça no GitHub Actions e usa o Gemini para filtrar clickbait
e pontuar relevância técnica.

Arquitetura:

1. **`/curadoria`** — pipeline Python (GitHub Actions, cron a cada 30 min): busca
   notícias, deduplica, envia ao Gemini para curadoria e grava os artigos aprovados
   no Firestore. A cada execução também apaga artigos já lidos e não favoritados
   após um período de carência (`CLEANUP_GRACE_HOURS`, padrão 48h), para os
   artigos não se acumularem indefinidamente.
2. **App Flutter** (`/lib`) — lê os artigos do Firestore e lê em voz alta usando o
   motor de TTS nativo do Android (`flutter_tts`), sem geração/armazenamento de
   áudio na nuvem.

Tudo roda em tiers 100% gratuitos: **sem** Vertex AI, Cloud Functions, Cloud
Scheduler ou Cloud Text-to-Speech, e sem precisar ativar faturamento no Google Cloud.

---

## 1. Criar o projeto Firebase (plano Spark, sem cartão de crédito)

1. Acesse o [Firebase Console](https://console.firebase.google.com/) e clique em
   **"Adicionar projeto"**.
2. Dê um nome (ex: `newsflow-pessoal`) e conclua a criação. O plano **Spark**
   (gratuito) é o padrão — não é necessário adicionar cartão de crédito.
3. Dentro do projeto, vá em **Compilação → Firestore Database → Criar banco de
   dados**. Escolha uma região (ex: `southamerica-east1`) e comece em **modo de
   produção** (as regras de segurança já estão neste repo em `firestore.rules`).
4. Instale a Firebase CLI e faça login:
   ```bash
   npm install -g firebase-tools
   firebase login
   ```
5. Na raiz do repo, associe o CLI ao projeto e publique as regras e o índice
   composto necessários para o feed:
   ```bash
   firebase use --add          # selecione o projeto criado
   firebase deploy --only firestore:rules,firestore:indexes
   ```
   O índice composto `(relevance_score desc, curated_at desc)` (definido em
   `firestore.indexes.json`) é o que permite a query principal do feed do app.

### 1.1 Gerar a service account (para o pipeline de curadoria)

1. No Firebase Console: **Configurações do projeto → Contas de serviço → Gerar
   nova chave privada**. Isso baixa um arquivo JSON.
2. **Nunca** commite esse arquivo no git. Guarde-o localmente (ex:
   `curadoria/service-account.json`, já ignorado pelo `.gitignore`) para testar
   localmente, e cole o conteúdo como secret no GitHub Actions (passo 3).

### 1.2 Configurar o app Flutter com o Firebase

1. Instale a FlutterFire CLI:
   ```bash
   dart pub global activate flutterfire_cli
   ```
2. Na raiz do repo, rode:
   ```bash
   flutterfire configure
   ```
   Selecione o projeto Firebase criado e a plataforma **Android**. Isso
   sobrescreve `lib/firebase_options.dart` (que hoje contém apenas valores de
   placeholder) com as credenciais reais do seu projeto, e também gera/atualiza
   `android/app/google-services.json`.

---

## 2. Gerar a chave do Gemini no Google AI Studio

1. Acesse o [Google AI Studio](https://aistudio.google.com/apikey).
2. Clique em **"Create API key"** e escolha (ou crie) um projeto Google Cloud
   associado — não é necessário ativar faturamento para usar o tier gratuito.
3. Copie a chave gerada. Ela será usada como o secret `GEMINI_API_KEY`.

O pipeline usa o modelo `gemini-3.1-flash-lite` por padrão (tier gratuito, ~1000
req/dia, 15 req/min). Se precisar trocar de modelo, defina a variável de
ambiente/secret `GEMINI_MODEL`.

---

## 3. Configurar os secrets do GitHub Actions

No repositório do GitHub: **Settings → Secrets and variables → Actions → New
repository secret**. Crie:

| Secret | Valor |
|---|---|
| `GEMINI_API_KEY` | A chave gerada no AI Studio (passo 2). |
| `FIRESTORE_SERVICE_ACCOUNT_JSON` | O conteúdo do JSON da service account (passo 1.1) — pode colar o JSON bruto ou o mesmo conteúdo em base64, o workflow detecta automaticamente. |

O workflow `.github/workflows/curadoria.yml` já está configurado para rodar a
cada 30 minutos (`cron: '*/30 * * * *'`) e também pode ser disparado manualmente
pela aba **Actions → Curadoria de Notícias Tech → Run workflow**.

### Testar o pipeline localmente antes de subir pro Actions

```bash
cd curadoria
cp .env.example .env        # preencha GEMINI_API_KEY e GOOGLE_APPLICATION_CREDENTIALS
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

O script imprime um resumo no final: quantos itens foram ingeridos, quantos
descartados por duplicidade, quantos aprovados/reprovados pelo Gemini e quantos
efetivamente salvos no Firestore.

---

## 4. Rodar e gerar o APK do app Flutter

Pré-requisitos: [Flutter SDK](https://docs.flutter.dev/get-started/install) e
Android SDK instalados (via Android Studio), com um dispositivo Android físico
conectado (ou emulador).

```bash
flutter pub get
flutter run          # roda em um dispositivo/emulador conectado
```

### Gerar o APK de release

```bash
flutter build apk --release
```

O APK gerado fica em `build/app/outputs/flutter-apk/app-release.apk`.

### Instalar no seu celular Android

1. Habilite **"Fontes desconhecidas"** / **"Instalar apps desconhecidos"** nas
   configurações do Android para o app que você vai usar para transferir o
   arquivo (ex: seu gerenciador de arquivos ou o navegador).
2. Transfira o `app-release.apk` para o celular (cabo USB, `adb install
   build/app/outputs/flutter-apk/app-release.apk`, ou upload para um serviço
   de arquivos pessoal).
3. Abra o arquivo no celular e confirme a instalação.

---

## Estrutura do repositório

```
/curadoria                          # Pipeline Python de ingestão e curadoria
  ├── ingest.py                     # Hacker News, Dev.to, GitHub Blog RSS, ArXiv
  ├── curate.py                     # Curadoria via Gemini (google-genai)
  ├── firestore_client.py           # Leitura/gravação no Firestore
  ├── text_utils.py                 # Normalização de título + hash para dedupe
  ├── main.py                       # Orquestra o pipeline completo
  ├── requirements.txt
  └── .env.example
/.github/workflows/curadoria.yml    # Cron a cada 30 min + workflow_dispatch
/firestore.rules                    # Leitura pública, escrita restrita a `read`/`read_at`/`favorite`
/firestore.indexes.json             # Índice composto (relevance_score desc, curated_at desc)
/lib
  ├── main.dart                     # Init do Firebase, tema escuro padrão
  ├── firebase_options.dart         # Gerado por `flutterfire configure`
  ├── models/article.dart
  ├── services/firestore_service.dart
  ├── services/tts_service.dart
  ├── providers/providers.dart      # State management (Riverpod)
  ├── screens/feed_screen.dart
  ├── screens/article_detail_screen.dart
  ├── screens/settings_screen.dart
  └── widgets/article_card.dart
```

## Limitações intencionais (uso pessoal)

- Sem autenticação de usuário: o Firestore permite leitura pública (dados não
  sensíveis — apenas um feed de notícias) e escrita restrita a marcar artigos
  como lidos.
- Sem testes automatizados de UI — o app foi validado com `flutter analyze`
  (sem erros) e `flutter pub get`; a build final do APK deve ser gerada e
  testada em uma máquina com Android SDK instalado.
- Sem Cloud Functions, Cloud Scheduler, Vertex AI ou Cloud Text-to-Speech em
  nenhuma parte da solução — tudo roda no tier gratuito do GitHub Actions, do
  Firebase (Spark) e do Google AI Studio.
