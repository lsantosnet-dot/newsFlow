# NewsFlow — App Pessoal de Notícias com Curadoria Customizável

App de uso **pessoal e exclusivo** (Flutter + Firestore + TTS nativo) para acompanhar
notícias curadas automaticamente por um pipeline Python que roda de graça no GitHub
Actions e usa o Gemini para filtrar clickbait e pontuar relevância.

**As fontes e os critérios de curadoria são configuráveis pelo app**: você define
perfis (Tecnologia, Política BR, Política Mundial, Economia, ou qualquer outro que
criar) e alterna entre eles sem tocar em código.

Arquitetura:

1. **`/curadoria`** — pipeline Python (GitHub Actions, cron a cada 30 min): carrega
   o perfil ativo do Firestore, busca notícias nas fontes dele, deduplica, envia ao
   Gemini com os critérios do perfil e grava os artigos aprovados. A cada execução
   também roda as rotinas de limpeza (ver [Limpeza automática](#limpeza-automática)).
2. **App Flutter** (`/lib`) — lê os artigos do perfil ativo e lê em voz alta usando o
   motor de TTS nativo do Android (`flutter_tts`), sem geração/armazenamento de
   áudio na nuvem. Também é onde você cria, edita e ativa os perfis.

Tudo roda em tiers 100% gratuitos: **sem** Vertex AI, Cloud Functions, Cloud
Scheduler ou Cloud Text-to-Speech, e sem precisar ativar faturamento no Google Cloud.

---

## Perfis de curadoria

Um **perfil** descreve *o que buscar* (`sources`) e *como filtrar* (`curation`).
Fica na coleção `profiles` do Firestore e é editável pelo app.

**Apenas um perfil fica ativo por vez** — é o que o pipeline cura e o que o feed
exibe. Trocar de perfil **não apaga nada**: os artigos dos outros perfis continuam
no Firestore e reaparecem quando você voltar para eles.

### Os 4 presets

O app semeia estes perfis no primeiro launch (Tecnologia ativo por padrão). Todos
podem ser editados, duplicados ou apagados, e servem de template para perfis novos.

| Perfil | Fontes |
|---|---|
| **Tecnologia** | Hacker News, Dev.to, GitHub Blog, InfoQ, Stack Overflow Blog, Martin Fowler, TechCrunch |
| **Política BR** | g1 Política, Folha (Poder), Agência Brasil, Poder360, O Globo (Política) |
| **Política Mundial** | BBC World, BBC Brasil, g1 Mundo, The Guardian, The New York Times, Al Jazeera, The Economist |
| **Economia** | g1 Economia, Folha (Mercado), InfoMoney, Exame |

Os perfis de política e economia usam personas que pedem **enquadramento factual e
neutro**, já que as fontes têm linhas editoriais distintas.

### Tipos de fonte

Cada fonte declara um `type`, resolvido no registry `SOURCE_ADAPTERS` de
[`ingest.py`](curadoria/ingest.py):

| `type` | Parâmetros | Observação |
|---|---|---|
| `rss` | `url`, `limit` | Cobre a maioria dos casos. Aceita RSS 2.0, Atom e RDF. |
| `hackernews` | `min_score`, `min_comments`, `limit` | API da HN |
| `devto` | `tags`, `per_tag` | API do Dev.to |
| `arxiv` | `categories`, `limit` | Papers acadêmicos |

**Adicionar um feed RSS é só dado** — cole a URL no app, sem mexer em Python. Para
os outros tipos, edite o JSON do perfil no Firestore.

### Limpeza automática

O pipeline roda três rotinas ao fim de cada execução. **Favoritos nunca são
apagados** em nenhuma delas.

| Rotina | O que apaga | Configuração |
|---|---|---|
| Artigos lidos | Lidos e não favoritados, passada a carência | `cleanup_grace_hours` (48h) |
| Perfis inativos | Artigos antigos de perfis que não estão ativos | `inactive_retention_days` (30 dias) |
| Purge sob demanda | O que você escolher ao editar um perfil | `pending_cleanup` |

Ao salvar uma edição que muda fontes ou critérios, o app pergunta o que fazer com
os artigos curados sob os critérios antigos:

- **Manter tudo** — nada é apagado; a nova config vale só para os próximos artigos.
- **Limpar não lidos** (padrão) — apaga os não lidos, preserva lidos e favoritos.
- **Limpar tudo** — apaga tudo do perfil, exceto favoritos.

A limpeza é **executada pelo pipeline**, não pelo app: as regras do Firestore
proíbem `delete` no cliente. Na prática, o purge acontece no próximo ciclo (até 30
minutos), ou imediatamente se você disparar o workflow manualmente.

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
   Os índices compostos (definidos em `firestore.indexes.json`) são escopados por
   `profile_id` — o principal é `(profile_id asc, relevance_score desc, curated_at
   desc)`, que permite a query do feed. **Publique os índices antes do primeiro
   run**, senão as queries falham.

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
pela aba **Actions → Curadoria de Notícias → Run workflow** (útil para aplicar
na hora um purge pendente, em vez de esperar o próximo ciclo).

### Testar o pipeline localmente antes de subir pro Actions

```bash
cd curadoria
cp .env.example .env        # preencha GEMINI_API_KEY e GOOGLE_APPLICATION_CREDENTIALS
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

O script imprime um resumo no final: qual perfil foi usado, quantos itens foram
ingeridos, quantos descartados por duplicidade, quantos aprovados/reprovados pelo
Gemini, quantos salvos no Firestore e quantos apagados por cada rotina de limpeza.

Se nenhum perfil estiver ativo, o pipeline encerra sem erro avisando que não há
trabalho — abra o app (ou rode `python migrate.py`) para semear os perfis.

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
  ├── ingest.py                     # Registry de adaptadores (rss, hackernews, devto, arxiv)
  ├── curate.py                     # Curadoria via Gemini, prompt montado do perfil
  ├── firestore_client.py           # Perfis, dedupe, gravação e as 3 rotinas de limpeza
  ├── text_utils.py                 # Normalização de título + hash para dedupe
  ├── main.py                       # Orquestra o pipeline do perfil ativo
  ├── migrate.py                    # Migração one-off para o modelo de perfis
  ├── requirements.txt
  └── .env.example
/assets/presets/profiles.json       # Os 4 perfis prontos (seed + templates)
/.github/workflows/curadoria.yml    # Cron a cada 30 min + workflow_dispatch
/firestore.rules                    # Leitura pública; `articles` só aceita read/favorite do app
/firestore.indexes.json             # Índices compostos escopados por profile_id
/lib
  ├── main.dart                     # Init do Firebase, tema escuro padrão
  ├── firebase_options.dart         # Gerado por `flutterfire configure`
  ├── models/article.dart
  ├── models/profile.dart           # Profile, ProfileSource, CurationConfig
  ├── services/firestore_service.dart
  ├── services/profile_service.dart # CRUD de perfis + ativação transacional
  ├── services/tts_service.dart
  ├── providers/providers.dart      # State management (Riverpod)
  ├── screens/feed_screen.dart
  ├── screens/article_detail_screen.dart
  ├── screens/profiles_screen.dart      # Lista, ativa, duplica e apaga perfis
  ├── screens/profile_edit_screen.dart  # Edita fontes e critérios de curadoria
  ├── screens/settings_screen.dart
  └── widgets/article_card.dart
```

## Migração (se você já tinha o NewsFlow rodando)

Os artigos gravados antes desta mudança não têm `profile_id` e sumiriam do feed.
Rode a migração **uma vez**, depois de publicar os índices novos:

```bash
firebase deploy --only firestore:rules,firestore:indexes   # publique ANTES

cd curadoria
python migrate.py --dry-run    # confira o que será feito
python migrate.py              # aplica
```

O script semeia os 4 perfis (se a coleção `profiles` estiver vazia) e marca os
artigos existentes com `profile_id=tech`. É idempotente — rodar de novo não
duplica nada nem sobrescreve perfis que você já editou.

## Limitações intencionais (uso pessoal)

- Sem autenticação de usuário: o Firestore permite leitura pública (dados não
  sensíveis — apenas um feed de notícias). Em `articles`, o app só pode marcar
  como lido/favorito; em `profiles`, pode escrever livremente (exceto valores
  inválidos de `pending_cleanup`), já que é o app que gerencia os perfis.
- **Um perfil ativo por vez.** Vários perfis podem coexistir com seus artigos
  guardados, mas o pipeline cura apenas o ativo — assim o consumo da cota do
  Gemini não cresce com o número de perfis.
- O purge de artigos ao editar um perfil não é instantâneo: acontece no próximo
  ciclo do pipeline (até 30 min), ou ao disparar o workflow manualmente.
- Sem testes automatizados de UI — o app foi validado com `flutter analyze`
  (sem erros) e `flutter pub get`; a build final do APK deve ser gerada e
  testada em uma máquina com Android SDK instalado. A lógica do pipeline
  (perfis, dedupe por perfil e as três rotinas de limpeza) foi validada com
  testes de integração contra um Firestore fake em memória.
- Sem Cloud Functions, Cloud Scheduler, Vertex AI ou Cloud Text-to-Speech em
  nenhuma parte da solução — tudo roda no tier gratuito do GitHub Actions, do
  Firebase (Spark) e do Google AI Studio.
