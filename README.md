# NewsFlow — App Pessoal de Notícias com Curadoria Customizável

App de uso **pessoal e exclusivo** (Flutter + Firestore + TTS nativo) para acompanhar
notícias curadas automaticamente por um pipeline Python que roda de graça no GitHub
Actions e usa o Gemini para filtrar clickbait e pontuar relevância.

**As fontes e os critérios de curadoria são configuráveis pelo app**: você define
perfis (Tecnologia, Política BR, Política Mundial, Economia, ou qualquer outro que
criar). O pipeline cura **todos** os perfis a cada execução, e você alterna entre
eles no feed sem tocar em código.

Arquitetura:

1. **`/curadoria`** — pipeline Python (GitHub Actions, cron a cada 30 min): para
   cada perfil do Firestore, busca notícias nas fontes dele, descarta o que já foi
   avaliado, envia o resto ao Gemini em lotes com os critérios do perfil e grava os
   artigos aprovados. A cada execução também roda as rotinas de limpeza (ver
   [Limpeza automática](#limpeza-automática)).
2. **App Flutter** (`/lib`) — lê os artigos do perfil ativo e lê em voz alta usando o
   motor de TTS nativo do Android (`flutter_tts`), sem geração/armazenamento de
   áudio na nuvem. Também é onde você cria, edita e ativa os perfis.

Tudo roda em tiers 100% gratuitos: **sem** Vertex AI, Cloud Functions, Cloud
Scheduler ou Cloud Text-to-Speech, e sem precisar ativar faturamento no Google Cloud.

---

## Perfis de curadoria

Um **perfil** descreve *o que buscar* (`sources`) e *como filtrar* (`curation`).
Fica na coleção `profiles` do Firestore e é editável pelo app.

**Todos os perfis são curados a cada execução**, cada um com suas fontes, persona,
critérios e `min_score`; os artigos ficam separados por `profile_id`.

`active` significa só **"o perfil que estou vendo no app"** (um por vez). Ele não
decide mais o que é curado; serve para:

- o feed saber qual perfil exibir;
- o pipeline processar esse perfil **primeiro** (se o orçamento de tempo acabar,
  quem fica para o próximo ciclo são os outros);
- a [retenção de perfis não exibidos](#limpeza-automática) poupar os artigos dele.

Trocar de perfil **não apaga nada**: os artigos dos outros perfis continuam no
Firestore, recebendo notícias novas, e aparecem quando você alternar para eles.

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

O pipeline roda estas rotinas no início de cada execução (antes da curadoria,
que é a parte lenta). **Favoritos nunca são apagados** em nenhuma delas.

| Rotina | O que apaga | Configuração |
|---|---|---|
| Artigos lidos | Lidos e não favoritados, de todos os perfis, sem carência | — |
| Perfis não exibidos | Artigos antigos dos perfis que não estão `active` no app | `inactive_retention_days` (30 dias) |
| Purge sob demanda | O que você escolher ao editar um perfil (verificado em todos os perfis) | `pending_cleanup` |
| Memória de itens vistos | Marcas da coleção `seen` mais antigas que a janela de dedupe | `dedupe_window_days` (7 dias) |

Como todos os perfis recebem artigos, os que você não está vendo acumulam até a
retenção de 30 dias; ao voltar para um deles, o feed mostra o que entrou nesse
período.

Ao salvar uma edição que muda fontes ou critérios, o app pergunta o que fazer com
os artigos curados sob os critérios antigos:

- **Manter tudo** — nada é apagado; a nova config vale só para os próximos artigos.
- **Limpar não lidos** (padrão) — apaga os não lidos, preserva lidos e favoritos.
- **Limpar tudo** — apaga tudo do perfil, exceto favoritos.

A limpeza é **executada pelo pipeline**, não pelo app: as regras do Firestore
proíbem `delete` no cliente. Na prática, o purge acontece no próximo ciclo (até 30
minutos), ou imediatamente se você disparar o workflow manualmente.

### Limpeza manual (purge-oldest)

O workflow **Apagar Artigos Mais Antigos** (`purge-oldest.yml`, só disparo manual)
tem dois modos. Em ambos, favoritos nunca são apagados e o **dry-run vem ligado**:
revise o log antes de rodar de novo com `dry_run: false`.

| Modo | Como | O que apaga |
|---|---|---|
| Por quantidade (padrão) | `limit` (50) | Os N artigos mais antigos por `curated_at` |
| Por idade | `older_than_days` (ex.: 14) | **Todos** os não lidos com `curated_at` mais antigo que isso (ignora `limit`) |

O escopo padrão é `all` (todos os perfis); `active` restringe ao perfil exibido no
app. Localmente: `python purge_oldest.py --older-than-days 14 --dry-run`. Não há
agendamento automático de exclusão — o bloco `schedule` do workflow fica
comentado de propósito.

---

## Economia de cota (Gemini e Firestore)

Curar vários perfis multiplicaria as chamadas ao Gemini. Duas coisas evitam isso,
sem mudar o formato nem os critérios dos artigos:

- **Coleção `seen`** — todo item que o Gemini avaliou com sucesso (aprovado *ou*
  reprovado) é marcado em `seen/{profile_id}_{title_hash}` (`profile_id`,
  `title_hash`, `seen_at`, `config_version`). Nos ciclos seguintes ele é
  descartado no dedupe, em vez de ser reenviado a cada 30 min. Itens que falharam
  na curadoria, que não puderam ser gravados ou que ficaram de fora por tempo/teto
  **não** são marcados, e voltam no próximo ciclo. Ao editar fontes/critérios de um
  perfil no app, `config_version` sobe e as marcas antigas deixam de valer (tudo é
  reavaliado com as regras novas). O dedupe lê `seen` em lote por ID (`get_all`) e
  só consulta `articles` para o que não estava em `seen`. O app não acessa `seen`
  (as regras negam).
- **Curadoria em lote** — vários itens vão numa mesma chamada, com o mesmo system
  prompt do perfil; cada um volta com os mesmos campos de antes, mais um `index`.
  Itens reprovados podem voltar com resumo/TTS vazios (não são publicados). Se a
  resposta vier incompleta ou inválida, só os itens afetados são reenviados, em
  lotes menores, com o mesmo retry/backoff.

### Variáveis de ambiente do pipeline

Todas opcionais. No GitHub Actions, as três primeiras podem ser definidas em
**Settings → Secrets and variables → Actions → Variables** (vazio = padrão).

| Variável | Padrão | O que faz |
|---|---|---|
| `MAX_ITEMS_PER_PROFILE_PER_RUN` | 40 | Teto de itens que cada perfil manda ao Gemini por execução (os mais recentes primeiro). O resto fica para o próximo ciclo. |
| `CURATION_BATCH_SIZE` | 8 | Itens por chamada ao Gemini. |
| `CURATION_RAW_CONTENT_MAX_CHARS` | 1500 | Quanto do conteúdo bruto de cada item vai no prompt. |
| `CURATION_TIME_BUDGET_SECONDS` | 600 | Orçamento de tempo da curadoria, **compartilhado por todos os perfis** da execução. |
| `GEMINI_REQUEST_DELAY_SECONDS` | 4 | Intervalo mínimo entre chamadas ao Gemini (15 req/min no tier gratuito). |
| `DEDUPE_WINDOW_DAYS` | 7 | Janela de dedupe/`seen` quando o perfil não define `dedupe_window_days`. |

### Estimativa de consumo diário

Com os 4 presets, cron de 30 min (≤ 48 execuções/dia; o GitHub costuma atrasar ou
pular algumas) e ~10 itens realmente novos por perfil a cada ciclo:

| Recurso | Estimativa | Limite gratuito |
|---|---|---|
| Requisições ao Gemini | ~400/dia (2 lotes × 4 perfis × 48); pior caso sustentado ~960 | ~1000/dia |
| Leituras no Firestore (pipeline) | ~12 mil/dia (≈ 250 por execução, a maior parte o `get_all` de `seen`) | 50 mil/dia |
| Escritas/exclusões no Firestore | ~4–5 mil/dia (marcas `seen` + expiração delas + artigos) | 20 mil/dia |

O pior caso do Gemini (40 itens novos por perfil em todo ciclo) não se sustenta na
prática — as fontes não publicam nesse ritmo —, mas acontece pontualmente depois
de editar os critérios de vários perfis. Se apertar, reduza
`MAX_ITEMS_PER_PROFILE_PER_RUN` ou aumente `CURATION_BATCH_SIZE`.

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
   O índice `(profile_id asc, curated_at asc)` deixa a limpeza de perfis não
   exibidos ler só os artigos vencidos; sem ele, ela cai num modo mais caro (lê o
   perfil inteiro) e avisa no log. Ao atualizar o repo, rode o mesmo `firebase
   deploy` de novo para publicar regras e índices novos.

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
na hora um purge pendente, em vez de esperar o próximo ciclo). Os ajustes
opcionais (`MAX_ITEMS_PER_PROFILE_PER_RUN`, `CURATION_BATCH_SIZE`,
`CURATION_RAW_CONTENT_MAX_CHARS`) entram como *Variables* do repositório — ver
[Variáveis de ambiente](#variáveis-de-ambiente-do-pipeline).

### Testar o pipeline localmente antes de subir pro Actions

```bash
cd curadoria
cp .env.example .env        # preencha GEMINI_API_KEY e GOOGLE_APPLICATION_CREDENTIALS
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

O script imprime um resumo no final, por perfil e no total: itens ingeridos,
descartados por duplicidade ou já vistos, acima do teto, enviados ao Gemini,
aprovados/reprovados, falhas, salvos no Firestore, marcados como vistos e quantos
foram apagados por cada rotina de limpeza. Um perfil com erro (feed fora do ar,
Gemini, Firestore) aparece marcado no resumo sem interromper os demais.

Se não houver nenhum perfil, o pipeline encerra sem erro avisando que não há
trabalho — abra o app (ou rode `python migrate.py`) para semear os perfis.

Para rodar os testes do pipeline (sem rede, com Firestore e Gemini falsos):

```bash
pip install -r requirements-dev.txt
python -m pytest tests
```

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

## Newsletter por email

No menu **⋮** (canto superior direito do feed), a opção **Newsletter** monta um
email pronto para enviar com os artigos **não lidos que o feed já carregou** —
respeitando a tag, o filtro e a ordenação ativos na tela. O scroll é infinito e
não tem páginas separadas: o que entra é o que está aberto no feed no momento,
então role até carregar tudo que você quer incluir antes de abrir a tela.

Cada artigo entra como título, fonte, data, resumo da curadoria, íntegra e link
da fonte original. Os três últimos blocos têm toggles: a íntegra é o `tts_text`
(a versão longa que o modo podcast lê em voz alta), então uma newsletter com 15
artigos pode passar de dezenas de milhares de caracteres — o contador embaixo da
prévia mostra o tamanho antes do envio.

> O corpo do artigo original **não** é guardado no Firestore: o pipeline usa o
> conteúdo bruto só como entrada do Gemini e descarta. A "íntegra" da newsletter
> é o texto reescrito para narração; o artigo original fica no link.

**Compartilhar** abre o share sheet do Android (Gmail, Outlook, o que estiver
instalado) com o texto no corpo e um assunto sugerido. **Copiar** joga o mesmo
texto na área de transferência. O toggle *Marcar como lidos ao enviar* fecha os
artigos incluídos num único batch — e não dispara se você cancelar o share sheet.

---

## Estrutura do repositório

```
/curadoria                          # Pipeline Python de ingestão e curadoria
  ├── ingest.py                     # Registry de adaptadores (rss, hackernews, devto, arxiv)
  ├── curate.py                     # Curadoria via Gemini em lote, prompt montado do perfil
  ├── firestore_client.py           # Perfis, dedupe (seen + artigos), gravação e limpezas
  ├── text_utils.py                 # Normalização de título + hash para dedupe
  ├── main.py                       # Orquestra o pipeline para todos os perfis
  ├── migrate.py                    # Migração one-off para o modelo de perfis
  ├── purge_oldest.py               # Limpeza manual: N mais antigos ou não lidos por idade
  ├── requirements.txt
  ├── requirements-dev.txt          # + pytest
  ├── tests/                        # pytest com Firestore/Gemini falsos
  └── .env.example
/assets/presets/profiles.json       # Os 4 perfis prontos (seed + templates)
/.github/workflows/curadoria.yml    # Cron a cada 30 min + workflow_dispatch
/.github/workflows/purge-oldest.yml # Manual: N mais antigos ou não lidos por idade (dry-run por padrão)
/firestore.rules                    # Leitura pública; `articles` só aceita read/favorite do app; `seen` fechado
/firestore.indexes.json             # Índices compostos escopados por profile_id
/lib
  ├── main.dart                     # Init do Firebase, tema escuro padrão
  ├── firebase_options.dart         # Gerado por `flutterfire configure`
  ├── models/article.dart
  ├── models/profile.dart           # Profile, ProfileSource, CurationConfig
  ├── services/firestore_service.dart
  ├── services/profile_service.dart # CRUD de perfis + ativação transacional
  ├── services/tts_service.dart
  ├── services/newsletter_builder.dart # Artigos não lidos -> texto da newsletter
  ├── providers/providers.dart      # State management (Riverpod)
  ├── screens/feed_screen.dart
  ├── screens/article_detail_screen.dart
  ├── screens/profiles_screen.dart      # Lista, ativa, duplica e apaga perfis
  ├── screens/profile_edit_screen.dart  # Edita fontes e critérios de curadoria
  ├── screens/settings_screen.dart
  ├── screens/newsletter_screen.dart    # Prévia da newsletter + copiar/compartilhar
  └── widgets/article_card.dart
/test/newsletter_builder_test.dart  # Testes do formatador da newsletter
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
- **Um perfil exibido por vez.** O pipeline cura todos os perfis; `active` só
  escolhe qual o feed mostra. O consumo da cota do Gemini cresce com o número de
  perfis e de notícias novas, contido pela coleção `seen`, pela curadoria em lote
  e pelo teto por perfil (ver [Economia de cota](#economia-de-cota-gemini-e-firestore)).
  Com muito mais perfis que os 4 presets, revise as estimativas.
- O purge de artigos ao editar um perfil não é instantâneo: acontece no próximo
  ciclo do pipeline (até 30 min), ou ao disparar o workflow manualmente.
- Sem testes automatizados de UI — o app foi validado com `flutter analyze`
  (sem erros) e `flutter pub get`; a build final do APK deve ser gerada e
  testada em uma máquina com Android SDK instalado. A lógica do pipeline
  (curadoria em lote, dedupe via `seen`, orquestração multi-perfil e o purge
  manual) tem testes em `curadoria/tests`, contra Firestore e Gemini falsos.
- Sem Cloud Functions, Cloud Scheduler, Vertex AI ou Cloud Text-to-Speech em
  nenhuma parte da solução — tudo roda no tier gratuito do GitHub Actions, do
  Firebase (Spark) e do Google AI Studio.
