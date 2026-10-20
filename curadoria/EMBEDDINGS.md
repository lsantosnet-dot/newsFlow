# Dedupe semântico com embeddings: guia de estudo

Este guia explica o que foi construído em `embeddings.py` e `semantic.py`, por
que funciona e como colocar em produção. No fim há exercícios para continuar
aprendendo.

## O problema

O dedupe original compara o **hash do título normalizado**. Ele pega "Apple
lança iPhone 18" repetido em dois feeds, mas não percebe que estes três itens
são a mesma notícia:

| Fonte | Título |
|---|---|
| g1 | Copom eleva a Selic para 11% ao ano |
| Folha | BC sobe juros básicos em meio ponto percentual |
| Reuters | Brazil central bank hikes rate to 11% |

Os três iam ao Gemini (gastando cota) e chegavam ao seu feed como três artigos.

## A ideia: texto vira vetor

Um **modelo de embedding** transforma um texto numa lista de números, um vetor.
Aqui são 256 números. O modelo foi treinado para que textos com o mesmo
significado virem vetores que apontam para a mesma direção, mesmo sem
palavras em comum e até em idiomas diferentes.

Para medir "apontam para a mesma direção" usamos a **similaridade de cosseno**:

```
cos(a, b) = (a · b) / (|a| × |b|)
```

- `a · b` (produto escalar) = `a[0]*b[0] + a[1]*b[1] + ...`
- `|a|` (norma) = `sqrt(a[0]² + a[1]² + ...)`

Se os vetores são **normalizados** (norma 1), a fórmula vira só o produto
escalar. Por isso `embeddings.normalize()` roda logo que o vetor chega da API e
`cosine_similarity()` tem uma linha só.

Valores típicos com `gemini-embedding-001`:

| Par | Similaridade aproximada |
|---|---|
| Mesma notícia, fontes diferentes | 0.88 – 0.97 |
| Mesmo assunto, fatos diferentes ("Selic sobe" × "inflação de setembro") | 0.70 – 0.85 |
| Assuntos diferentes | < 0.65 |

Esses números são uma referência. Os do **seu** feed você mede com
`explore_similarity.py` (abaixo).

### Por que 256 dimensões e não 3072?

O `gemini-embedding-001` foi treinado com *Matryoshka Representation Learning*:
as primeiras dimensões carregam a maior parte do significado, como bonecas
russas. Truncar para 256 perde pouco para a pergunta "é a mesma notícia?" e
ocupa 12× menos espaço no Firestore. O efeito colateral é que o vetor truncado
não vem mais normalizado, e é por isso que `normalize()` existe.

### Por que `task_type="SEMANTIC_SIMILARITY"`?

O mesmo modelo gera vetores otimizados para tarefas diferentes. Para busca
(pergunta → documentos) usaria `RETRIEVAL_QUERY` e `RETRIEVAL_DOCUMENT`. Aqui
comparamos textos do mesmo tipo entre si, então é `SEMANTIC_SIMILARITY`.

## Como o pipeline usa isso

```
ingest → dedupe por hash → [dedupe semântico] → teto → Gemini → grava artigo + embedding
```

`semantic.find_semantic_duplicates()` faz duas comparações:

1. **Item novo × artigos já salvos.** Para cada item, uma consulta
   `find_nearest` no Firestore devolve o artigo salvo mais parecido do mesmo
   perfil. Se a similaridade passa do limiar, o item **não** vai ao Gemini: a
   fonte dele entra em `also_covered_by` do artigo existente, com
   `ArrayUnion`, uma operação atômica do Firestore que não duplica entradas.
2. **Item novo × itens novos da mesma rodada.** Compara todos os pares
   (n²/2 contas, instantâneo para 40 a 100 itens) e junta os parecidos com
   **union-find**, a mesma estrutura de `dedupe_articles.py`. De cada grupo, só o
   item com mais conteúdo vai ao Gemini, levando as outras fontes em
   `also_covered_by`.

No app, o card mostra "g1 · +2 fontes" e o detalhe lista os links.

### Busca vetorial no Firestore (KNN)

Comparar o item novo com *todos* os embeddings salvos do perfil exigiria baixar
centenas de documentos a cada execução, ou seja, dezenas de milhares de leituras
por dia, o que estoura o plano gratuito. O Firestore resolve isso com um
**índice vetorial**: você manda o vetor e ele devolve os K vizinhos mais
próximos (*K-nearest neighbors*), cobrando cerca de uma leitura por resultado.

```python
collection.where("profile_id", "==", pid) \
          .where("config_version", "==", v) \
          .find_nearest(vector_field="embedding", query_vector=Vector(vec),
                        limit=1, distance_measure=DistanceMeasure.DOT_PRODUCT,
                        distance_result_field="similarity")
```

`DOT_PRODUCT` com vetores normalizados é o próprio cosseno. O índice está em
`firestore.indexes.json` (os campos de igualdade vêm antes do campo vetorial).
O tipo `flat` faz busca exata. Com milhões de vetores se usaria um índice
aproximado (ANN), que troca um pouco de precisão por velocidade.

Os embeddings ficam numa coleção separada, `article_embeddings/{article_id}`,
para o app não baixar 256 números a cada artigo do feed.

### Union-find em 30 segundos

Cada item começa sendo seu próprio grupo (`parent[i] = i`). Para cada par
parecido, `parent[raiz(j)] = raiz(i)` junta os dois grupos. `find()` sobe a
cadeia de pais até a raiz e, no caminho, encurta a cadeia (*path halving*).
No fim, itens com a mesma raiz são o mesmo grupo.

Um detalhe: a união é **transitiva**. Se A≈B e B≈C, A e C ficam juntos mesmo
que entre si não passem do limiar. Isso se chama "efeito corrente" do
*single-linkage clustering*. Com um limiar alto, é raro e aceitável. O teste
`test_group_similar_is_transitive_and_keeps_order` mostra o caso.

## Colocando em produção

1. **Publicar o índice vetorial** (o resto do pipeline funciona sem ele, mas a
   comparação com artigos salvos fica desligada e o log avisa):

   ```bash
   firebase deploy --only firestore:rules,firestore:indexes
   ```

   A criação do índice leva alguns minutos.

2. **Deixar rodar em modo `log`** (o padrão) por um ou dois dias. Nada é
   descartado; o log do Actions mostra linhas como:

   ```
   [semantic] 0.912 'BC sobe juros básicos' (Folha) seria absorvido (modo log) pelo artigo Xy12...
   ```

   Os embeddings dos artigos aprovados já vão sendo gravados, então quando o
   modo `on` for ligado já existe uma base para comparar.

3. **Calibrar o limiar** com dados reais:

   ```bash
   cd curadoria
   python explore_similarity.py --top 40
   ```

   Procure na lista o ponto em que os pares deixam de ser "a mesma notícia" e
   passam a ser "o mesmo assunto". O limiar fica um pouco acima disso. Errar
   para cima (limiar alto) só deixa passar algumas duplicatas. Errar para baixo
   esconde notícias diferentes, o que é pior.

4. **Ligar:** em *Settings → Secrets and variables → Actions → Variables*, crie
   `SEMANTIC_DEDUPE_MODE=on` (e `SEMANTIC_DEDUPE_THRESHOLD`, se mudou o limiar).

## Custo

| Recurso | Uso |
|---|---|
| API de embeddings | 1 chamada por perfil por execução (até 100 textos cada), ~200/dia com 4 perfis. A cota é separada da do modelo de curadoria. |
| Leituras no Firestore | ~1 por item novo (busca vetorial), mais a limpeza diária |
| Escritas no Firestore | 1 embedding por artigo aprovado, mais a expiração deles |
| Gemini de curadoria | **Diminui**: notícias repetidas não são mais enviadas |

## Exercícios para continuar

Do mais simples ao mais ambicioso:

1. **Ver os números.** Rode `explore_similarity.py` em dois perfis diferentes.
   O histograma tem dois "morros" (assuntos diferentes × mesma notícia)? Onde
   fica o vale entre eles?
2. **Trocar o texto embeddado.** Em `embedding_text()`, teste só o título, ou
   título + 1000 caracteres. A separação entre os morros melhora ou piora?
3. **numpy.** Reescreva `group_similar()` com uma matriz: `M = V @ V.T` calcula
   todas as similaridades de uma vez. Meça a diferença de tempo com 1000 itens
   usando `timeit`.
4. **Linha do tempo de uma história.** Um mesmo assunto (ex.: "reforma
   tributária") aparece em notícias diferentes ao longo de semanas, com
   similaridade entre 0.75 e 0.85. Use `find_nearest` com `limit=5` e um
   limiar mais baixo para mostrar no detalhe do artigo "notícias anteriores
   sobre isto".
5. **Busca semântica no app.** Um campo de busca no feed: o texto digitado vira
   embedding (`task_type="RETRIEVAL_QUERY"`) e o Firestore devolve os artigos
   mais próximos. Isso exige gerar o embedding da busca. Dá para fazer direto
   do Flutter com a chave do Gemini, ou com uma rota HTTP pequena. Esse é o
   primeiro passo de um sistema de RAG ("pergunte às suas notícias").
6. **Agrupamento melhor.** Troque o single-linkage por *average-linkage* (um
   item só entra no grupo se a média das similaridades com os membros passar do
   limiar) e compare os grupos formados.
