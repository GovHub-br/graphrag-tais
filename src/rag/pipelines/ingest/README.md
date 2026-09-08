# Pipeline de ingestão — OpenMetadata → Neo4j + Qdrant

Esta pipeline é o **único caminho de escrita** do conhecimento de schema do
graphrag-tais. Ela lê os metadados do **OpenMetadata**, normaliza para um
contrato canônico (ADR-0011), grava o **grafo no Neo4j** e, só depois de o
grafo confirmar, gera embeddings e grava os **cards no Qdrant**.

Nada de dado de linha entra aqui: a pipeline lê apenas `Table` + `Column`
(nomes, tipos, descrições, tags, constraints e relações documentadas). Os dados
reais vivem no banco analítico (alcançado pelo Trino, ADR-0017) e são tocados só
na execução do SQL.

- Decisão de contrato: [ADR-0011](../../../../docs/adr/0011-contrato-detalhado-da-ingestao-openmetadata-neo4j-qdrant.md)
- Fonte: [ADR-0004](../../../../docs/adr/0004-openmetadata-como-fonte-de-metadados.md)
- Embeddings/híbrido: [ADR-0009](../../../../docs/adr/0009-modelo-de-embedding-e-composicao-do-retrieval-hibrido.md)

---

## 1. TL;DR — rodar a ingestão

```bash
make up
```

```bash
curl -X POST http://localhost:8000/ingest/run -H "Content-Type: application/json" -d '{}'
```

O corpo é **sempre `{}`**: a pipeline não aceita parâmetros por request. Todo o
escopo (instância do OM, service, database, schemas, adapter, perfil de
embedding) vem do `.env`. Isso é deliberado — o escopo é configuração de
ambiente, não argumento de chamada, para que duas execuções do mesmo ambiente
sejam reprodutíveis.

Uma execução completa do MCid leva alguns minutos (dominados pelo embedding
denso no Ollama). O endpoint é **síncrono**: ele só responde no fim.

Resposta de sucesso (HTTP 200):

```json
{
  "status": "completed",
  "scope_id": "mcid",
  "ingestion_run_id": "0f7c…",      // UUID novo a cada execução
  "source_version": "1.12.1",
  "tables": 74, "columns": 1215, "relationships": 1239, "cards": 1289,
  "relationships_by_type": {"HAS_COLUMN": 1215, "FOREIGN_KEY": 0, "SEMANTIC_JOIN": 24},
  "nodes_written": 1289,
  "relationships_written": 1239,
  "documents_written": 1289,
  "tables_rejected": 12,
  "tables_rejected_by_reason": {"missing_documentation_marker": 12}
}
```

**Qualquer falha vira HTTP não-2xx.** A pipeline não devolve `{"status":"erro"}`
com HTTP 200 de propósito: assim um agendador (Airflow) não registra falso
sucesso.

---

## 2. Pré-requisitos

| Requisito | Por quê | Como conferir |
|---|---|---|
| Docker Desktop ligado | sobe qdrant, neo4j, hayhooks | `docker ps` |
| **VPN/rede da lab** | Ollama (`10.0.0.10`) e o OpenMetadata são externos | `curl $OM_HOST/api/v1/system/version` |
| Ollama com o modelo de embedding baixado | embedding denso dos cards | `ollama list` |
| `.env` na raiz preenchido | escopo + credenciais (§4) | `grep OM_ .env` |
| Qdrant e Neo4j de pé | destino da escrita | `curl localhost:6333/healthz` · http://localhost:7474 |

⚠️ O `.env` **não pode ter cercas markdown (```)** — quebra o `docker compose`.

Mudança em `.env` **não** é recarregada pelo `--reload` do hayhooks (que só
observa `src/`). Depois de editar o `.env`:

```bash
docker compose up -d --no-deps hayhooks
```

---

## 3. Como funciona — componentes e ordem

```
OpenMetadataSource ─► MetadataRecordBuilder ─► GraphCardBuilder
   (REST v1)            (contrato canônico)      │  ├─ nodes ─────────┐
                                                 │  ├─ relationships ─┤
                                                 │  └─ documents ─┐   │
                                                                 │   ▼
                                                                 │  Neo4jGraphWriter
                                                                 │   │ nodes_written
                                                                 │   │ relationships_written
                                                                 ▼   ▼
                                                              GraphWriteGate
                                                                   │ documents
                                                                   ▼
                                          sparse (BM25/fastembed) ─► dense (Ollama) ─► Qdrant
```

| Componente | Arquivo | O que faz |
|---|---|---|
| `OpenMetadataSource` | [`components/sources/openmetadata_source.py`](../../components/sources/openmetadata_source.py) | Lê `GET /system/version` e pagina `GET /tables` por schema. Filtra o que está fora do escopo. Devolve **um snapshot completo** (nada é liberado antes de todas as páginas). |
| `MetadataRecordBuilder` | [`components/converters/metadata_record_builder.py`](../../components/converters/metadata_record_builder.py) | Valida e normaliza em `tables` / `columns` / `relationships` canônicos. Deriva `HAS_COLUMN`, `FOREIGN_KEY` (de `tableConstraints`) e chama o adapter das relações semânticas. Gera `relationship_id` estável (SHA-256). |
| `relationship_adapters` | [`components/sources/relationship_adapters.py`](../../components/sources/relationship_adapters.py) | Parser da Custom Property Markdown do MCid → `SEMANTIC_JOIN`. Isolado do núcleo: trocar de catálogo = trocar de adapter. |
| `GraphCardBuilder` | [`components/converters/graph_card_builder.py`](../../components/converters/graph_card_builder.py) | Projeta o canônico em (a) nós/arestas do Neo4j e (b) **cards** do Qdrant, com `id` determinístico (`uuid5`). |
| `Neo4jGraphWriter` | [`components/writers/neo4j_graph_writer.py`](../../components/writers/neo4j_graph_writer.py) | Cria constraints, faz `MERGE` de nós e depois das arestas, em lotes de 500, **conferindo a contagem confirmada** por lote. |
| `GraphWriteGate` | [`components/writers/graph_write_gate.py`](../../components/writers/graph_write_gate.py) | Barreira: só libera os cards para embedding depois que o Neo4j devolveu contagens válidas. |
| Embedders | [`components/embedders/`](../../components/embedders/) | Esparso (BM25/fastembed, CPU local) → denso (Ollama). Nessa ordem, no mesmo `Document`. |
| `SafeDocumentWriter` | [`components/writers/safe_document_writer.py`](../../components/writers/safe_document_writer.py) | Upsert no Qdrant com `DuplicatePolicy.OVERWRITE` e `wait_result_from_api=True` (confirmação síncrona). |

**Por que grafo antes do vetor:** não há transação distribuída entre Neo4j e
Qdrant. A ordem escolhida prefere “grafo atualizado e vetor atrasado” a “card
recuperável apontando para um nó que não existe” — este segundo estado quebra o
retrieval em silêncio. Se o Qdrant falhar, o run falha e é reexecutado.

### Identidade e idempotência

| ID | Como é derivado | Consequência |
|---|---|---|
| `node_id` (Table/Column) | o **FQN** do OpenMetadata | `MERGE` converge; mudar descrição atualiza o mesmo nó |
| `relationship_id` | SHA-256 de tipo + pontas + `source_kind` + chave/regra | aresta duplicada nunca é criada |
| `card_id` | `{scope_id}\|table:{fqn}` / `{scope_id}\|column:{fqn}` | estável entre runs |
| `point_id` (Qdrant) | `uuid5(namespace(scope_id), card_id)` | reingestão **atualiza** o ponto, não duplica |
| `ingestion_run_id` | UUID novo por execução, gravado em nós/arestas/cards | permite saber qual run tocou cada objeto |

Reexecutar o mesmo snapshot converge: as contagens e todos os IDs se mantêm,
só o `ingestion_run_id` muda.

---

## 4. Integração com o OpenMetadata

### O que é lido

Um único endpoint de dados, paginado por cursor, **um schema por vez**:

```
GET {OM_HOST}/api/v1/tables
    ?databaseSchema={FQN do schema}
    &fields=columns,tableConstraints,tags,extension
    &include=non-deleted
    &limit={OM_PAGE_SIZE}
    &after={cursor}
```

Mais `GET /system/version`, que é validado contra `OM_EXPECTED_VERSION` **antes**
de qualquer leitura — versão diferente aborta o run (o parsing segue a API v1 e
varia por versão do OM).

Autenticação: `Authorization: Bearer {OM_JWT_TOKEN}`. Retry automático (com
backoff e respeito ao `Retry-After`) apenas para timeout, 429 e 5xx — 4xx falha
na hora.

A pipeline **não** consulta amostras, perfis, métricas, glossário nem lineage.

### Filtro de escopo (o que é aceito)

Cada tabela passa por uma bateria de checagens; o motivo da rejeição é contado
e devolvido em `tables_rejected_by_reason`:

| Motivo | Significado |
|---|---|
| `missing_table_fqn` | tabela sem `fullyQualifiedName` |
| `service_out_of_scope` | `service` ≠ `OM_SERVICE` |
| `database_out_of_scope` | `database` ≠ `OM_DATABASE` |
| `schema_out_of_scope` / `table_fqn_out_of_scope` | FQN não pertence ao schema pedido |
| `missing_documentation_marker` | não tem a Custom Property documental (só quando `OM_REQUIRE_DOCUMENTED_TABLES=true`) |

Se **nenhuma** tabela for aceita, o run falha com
`OpenMetadataContractError: Nenhuma tabela documentada foi aceita`.

### Relações semânticas (o coração do MCid)

O MCid **não declara FK física** — `FOREIGN_KEY` vem 0. A joinabilidade é
publicada em duas Custom Properties da tabela no OM:

- `mcidSemanticRelationships` (`OM_SEMANTIC_RELATIONSHIPS_PROPERTY`): Markdown
  com a seção `### Relações explícitas`;
- `mcidRelatedTables` (`OM_RELATED_TABLES_PROPERTY`): lista de referências que
  **confirma** cada contraparte citada no Markdown.

Gramática aceita por linha (adapter `mcid_markdown_v1`):

```
- **<classificação>** (origem|destino): `schema.tabela.coluna` → `schema.tabela.coluna`;
  chave `<semantic_key>`; JOIN `<join_rule>`; cardinalidade `<n:m>`; confiança `<alta|…>`.
```

Regras que o adapter impõe — todas viram **erro**, nunca aviso silencioso:

- o cabeçalho precisa declarar o catálogo `mcid-semantic-relationships`;
- classificação conhecida: *Contrato validado por teste dbt* → `dbt_relationships_test`,
  *Relação validada por agregados* → `aggregate_validated`;
- **“Relação candidata” é ignorada** (contada em `semantic_candidates_skipped`) — candidata não vira aresta;
- as duas pontas precisam existir no snapshot (logo, **os dois schemas precisam estar em `OM_SCHEMAS`**);
- a tabela que publica a relação precisa ser uma das pontas;
- a contraparte precisa aparecer em `mcidRelatedTables`;
- duas publicações divergentes da mesma relação = erro; iguais = dedup.

`sql_join_eligible` só é `true` quando `JOIN` é `exact`. Regras que exigem
normalização entram no grafo, mas **nunca** são oferecidas ao LLM como JOIN
autorizado.

---

## 5. Configuração (`.env`)

### Escopo e fonte

| Variável | Papel |
|---|---|
| `OM_HOST`, `OM_API_PATH`, `OM_JWT_TOKEN` | instância e credencial |
| `OM_EXPECTED_VERSION` | versão exigida (abortará se divergir) |
| `OM_SCOPE_ID` | rótulo do escopo; entra em todo nó/aresta/card e é o filtro do retrieval |
| `OM_SERVICE`, `OM_DATABASE` | service e database permitidos |
| `OM_SCHEMAS` | **allowlist** de FQNs `Service.database.schema`, separados por vírgula |
| `OM_REQUIRE_DOCUMENTED_TABLES` | aceita só tabelas com a Custom Property documental |
| `OM_RELATIONSHIP_ADAPTER` | `mcid_markdown_v1` ou `none`/vazio para desligar |
| `OM_PAGE_SIZE`, `OM_TIMEOUT`, `OM_MAX_RETRIES` | paginação e resiliência HTTP |

### Cards e embeddings

| Variável | Papel |
|---|---|
| `OM_CARD_SCHEMA_VERSION` | `v1` (layout rotulado original) ou `v2` (denso em significado) |
| `OM_EMBEDDING_PROFILE` | identificador **imutável** do perfil da collection; troque junto com o modelo |
| `OM_DOMAIN_LABELS` | JSON `{schema_curto: rótulo de negócio}` — **só v2** |
| `OM_VALUE_VOCAB` | JSON `{fqn: {coluna: [valores]}}` — **só v2**, essencial em tabela EAV |
| `OM_GRAIN_KEYS` | JSON `{fqn: descrição do grão}` — usado na serialização do subgrafo, **não** entra no card |
| `OLLAMA_EMBED_MODEL`, `QDRANT_EMBEDDING_DIM` | modelo denso e dimensão — **precisam casar** |
| `QDRANT_SPARSE_MODEL`, `SPARSE_LANGUAGE`, `SPARSE_FOLD_ACCENTS` | canal esparso; o idioma tem de ser o mesmo na ingestão e na query |
| `QDRANT_COLLECTION`, `QDRANT_USE_SPARSE` | destino e modo híbrido |
| `EMBED_BATCH_SIZE`, `WRITE_BATCH_SIZE` | lotes de embedding e de escrita |

> O texto do card é usado **apenas para gerar o embedding**. O contexto que
> chega ao LLM é remontado do Neo4j pelo `Neo4jSubgraphRetriever`, que só lê
> `node_id` e `card_type` do card. Ou seja: mexer no texto do card é seguro para
> a resposta — mas muda o retrieval e **exige reingestão + medição** (ADR-0012).

---

## 6. Verificando o resultado

### Neo4j (http://localhost:7474 · user `neo4j`, senha em `NEO4J_PASSWORD`)

```cypher
// Contagem por label e por tipo de relação
MATCH (n) RETURN labels(n) AS label, count(*) ORDER BY label;
MATCH ()-[r]->() RETURN type(r) AS tipo, count(*) ORDER BY tipo;
```

```cypher
// Tudo veio do mesmo run? (deve devolver 1 linha)
MATCH (n:Table) RETURN DISTINCT n.ingestion_run_id, count(*);
```

```cypher
// Joins que o gerador de SQL pode realmente usar
MATCH (a:Column)-[r:SEMANTIC_JOIN]->(b:Column)
WHERE r.sql_join_eligible
RETURN a.node_id, b.node_id, r.semantic_key, r.join_rule, r.validation;
```

```cypher
// Tabelas-ilha (sem nenhum join) — diagnóstico de cobertura do retrieval
MATCH (t:Table)
WHERE NOT EXISTS { MATCH (t)-[:HAS_COLUMN]->(:Column)-[:SEMANTIC_JOIN]-() }
RETURN count(t) AS ilhas;
```

### Qdrant (http://localhost:6333/dashboard)

```bash
curl -s "http://localhost:6333/collections/documents" | jq '.result.points_count, .result.config.params'
```

```bash
curl -s -X POST "http://localhost:6333/collections/documents/points/scroll" \
  -H 'Content-Type: application/json' \
  -d '{"limit":1,"with_payload":true,"with_vector":false}' | jq '.result.points[0].payload'
```

Confira no payload: `scope_id`, `card_type`, `node_id`, `card_schema_version`,
`embedding_profile`, `ingestion_run_id`, `content_hash`.

**A checagem que mais importa**, porque é a costura entre os dois bancos:
`points_count` do Qdrant == `nodes_written` da resposta, e todo `node_id` de
card resolve num nó do Neo4j. Card órfão = resultado recuperado que não vira
contexto.

### Fim-a-fim

```bash
curl -X POST http://localhost:8000/query/run -H "Content-Type: application/json" \
  -d '{"query": "quantos empreendimentos por município"}'
```

---

## 7. Agendamento, reingestão e passos manuais

### Agendamento

**Hoje não existe DAG neste repositório.** O `PipelineWrapper` foi escrito para
ser acionado por um agendador (por isso o contrato “exceção → HTTP não-2xx”),
mas o disparo atual é **manual, via `curl`**. Automatizar = criar uma DAG do
Airflow com um único `HttpOperator` para `POST /ingest/run` com corpo `{}`,
tratando não-2xx como falha da task. Sem retry cego: um run que falhou no meio
deixa o grafo à frente do vetor, e a reexecução é idempotente mas cara.

> Memória: com ~7.75 GiB no Docker Desktop, **Airflow + RAG + open-webui não
> cabem juntos** (o webui morre com exit 137).

### Quando é preciso reingerir

| Mudou | Reingerir? | Recriar a collection? |
|---|---|---|
| Metadados no OM (descrição, coluna, relação) | sim | não |
| `OM_CARD_SCHEMA_VERSION`, `OM_DOMAIN_LABELS`, `OM_VALUE_VOCAB` | sim | não |
| `SPARSE_LANGUAGE`, `SPARSE_FOLD_ACCENTS`, `QDRANT_SPARSE_MODEL` | sim (só o esparso muda, mas o run é inteiro) | não |
| `OLLAMA_EMBED_MODEL` / `QDRANT_EMBEDDING_DIM` | sim | **sim** |
| Só o lado da query (`QUERY_INSTRUCTION_ENABLED`, pesos, `EXPANSION_MODE`) | **não** | não |

Trocar o modelo denso sem recriar a collection deixa vetores de perfis
diferentes no mesmo espaço — as distâncias passam a não significar nada. Troque
`OM_EMBEDDING_PROFILE` junto, para que o payload registre o que foi usado.

### Reset manual (destrutivo — confirme o ambiente antes)

```bash
curl -X DELETE "http://localhost:6333/collections/documents"
```

```cypher
MATCH (n) DETACH DELETE n;
```

A collection é recriada sozinha na próxima ingestão, com a dimensão e a
similaridade do `.env`.

### O que **não** é automático (limitações conhecidas do v1)

Estes itens estão especificados na ADR-0011 (§7 e §8) mas **não implementados**:

1. **Sem preflight de stale/rename.** Tabela removida ou renomeada no OM não
   bloqueia o run (`status=blocked_by_stale` não existe no código).
2. **Sem remoção.** Nó, aresta e card obsoletos **permanecem** — `MERGE` e
   upsert só escrevem. Limpeza é manual (Cypher `DETACH DELETE` pelos
   `node_id` obsoletos, `DELETE /points` no Qdrant pelos `point_id`), ou reset
   completo + reingestão.
3. **Sem alias/blue-green no Qdrant.** A escrita é in-place na collection viva.
4. **Sem transação distribuída.** Falha no Qdrant deixa o grafo à frente; a
   correção é reexecutar.

O sintoma prático de (1)+(2): depois de um rename no catálogo, o retrieval passa
a ter **duas** entradas — a velha e a nova — e a velha continua sendo
recuperável. Depois de mexer no catálogo, confira a contagem de nós contra
`tables`/`columns` da resposta do run.

---

## 8. Erros comuns

| Mensagem | Causa | Correção |
|---|---|---|
| `Configuração OpenMetadata incompleta: …` | falta `OM_SCOPE_ID`/`SERVICE`/`DATABASE`/`SCHEMAS`/`EXPECTED_VERSION` | preencha o `.env` e recrie o hayhooks |
| `OM_SCHEMAS deve conter FQNs no escopo Service.database.<schema>` | schema com FQN curto ou de outro database | use os 3 segmentos completos |
| `Versão do OpenMetadata incompatível` | instância do OM subiu de versão | valide o parsing no Swagger `{OM_HOST}/docs` e ajuste `OM_EXPECTED_VERSION` |
| HTTP 401/403 na leitura | JWT expirado ou sem permissão | gere um novo `OM_JWT_TOKEN` |
| `Nenhuma tabela documentada foi aceita` | escopo errado ou nenhuma tabela com a Custom Property | confira `tables_rejected_by_reason`; para diagnosticar, `OM_REQUIRE_DOCUMENTED_TABLES=false` |
| `Relação semântica aponta para coluna fora do snapshot` | a contraparte do join está num schema fora de `OM_SCHEMAS` | inclua o schema, ou corrija a publicação no OM |
| `mcidRelatedTables não confirma <tabela>` | Markdown e lista de tabelas relacionadas divergem no OM | corrija a Custom Property |
| `Linha de relação semântica malformada` | gramática Markdown fora do contrato | ajuste a publicação (§4) |
| `Neo4j confirmou n/m nós` / `não encontrou todas as pontas` | escrita parcial ou aresta apontando para nó ausente | reexecute; se persistir, é bug de projeção |
| Timeout/erro do Ollama no embedding | VPN caída ou modelo não baixado | conecte a VPN, `ollama pull <modelo>` |
| `.env` alterado e nada mudou | `--reload` só observa `src/` | `docker compose up -d --no-deps hayhooks` |

Logs do run:

```bash
docker logs -f hayhooks
```

---

## 9. Testes

Os testes de contrato da ingestão rodam **offline**, com fixtures — não tocam em
OpenMetadata, Neo4j nem Qdrant:

```bash
python -m unittest tests.test_openmetadata_ingest
```

Cobrem: filtro de escopo, paginação por cursor, retry de 5xx, dedup de relação
semântica, FK composta inconsistente, estabilidade de `card_id`/`point_id` e o
Cypher com labels + `relationship_id` do writer.

Mudança que altere cards, relações ou embeddings altera o retrieval e, por
[ADR-0012](../../../../docs/adr/0012-desenvolvimento-orientado-a-metricas-no-retrieval.md),
exige medição antes/depois — ver [`eval/README.md`](../../../../eval/README.md).
