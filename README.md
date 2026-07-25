# graphrag-tais

**GraphRAG de metadados para text-to-SQL.** Dada uma pergunta em linguagem
natural, o sistema recupera o subgrafo de schema relevante (tabelas, colunas,
FKs, lineage) e gera a consulta **SQL**. Os metadados vêm do **OpenMetadata**.

Construído com [Haystack](https://haystack.deepset.ai/) 3 +
[Hayhooks](https://github.com/deepset-ai/hayhooks), combinando busca vetorial
**híbrida** (Qdrant, denso+esparso) com **grafo de conhecimento** (Neo4j), e
**Ollama** para embeddings e geração.

## Arquitetura (padrão canônico Qdrant → Neo4j)

```
PERGUNTA ─┬─► embedding denso (Ollama) ─┐
          └─► embedding esparso (SPLADE)┴─► Qdrant (busca híbrida)  ← acha os cards
                                               │  node_ids
                                               ▼
                                         Neo4j (Cypher)             ← percorre FK + lineage
                                               │  subgrafo de schema
                                               ▼
                                            LLM (Ollama)            ← escreve o SQL
```

- **Qdrant** = entrada semântica: 1 ponto por *card* (tabela/coluna/métrica/termo),
  com `node_id` no payload. Híbrido denso+esparso — o esparso casa identificadores
  exatos (`user_id`, `revenue`), o denso casa semântica ("faturamento").
- **Neo4j** = grafo puro percorrido por Cypher: nós tipados e relações (`HAS_COLUMN`,
  `FOREIGN_KEY`, `LINEAGE`, `IN_SCHEMA`, `USES`, `HAS_TERM`). É o que dá os JOINs.

> **Estado atual versus contrato-alvo:** esta seção descreve o protótipo que
> ainda está no código. A
> [ADR-0011](docs/adr/0011-contrato-detalhado-da-ingestao-openmetadata-neo4j-qdrant.md)
> reduz a próxima versão da ingestão a Table, Column e Relationship e lista as
> mudanças necessárias; MetricFlow, glossário, lineage e granularidade ficam
> fora daquele contrato.

### Modelo do grafo

| Nó | Origem (OpenMetadata) |
|----|----|
| `:Table` / `:Column` | tables + columns |
| `:MetricFlow` | metrics |
| `:Database` / `:Schema` | hierarquia da tabela |
| `:GlossaryTerm` | glossaryTerms |

Relações: `(:Table)-[:HAS_COLUMN]->(:Column)`, `(:Column)-[:FOREIGN_KEY]->(:Column)`,
`(:Table)-[:LINEAGE]->(:Table)`, `(:Table)-[:IN_SCHEMA]->(:Schema)-[:IN_DATABASE]->(:Database)`,
`(:Column)-[:HAS_TERM]->(:GlossaryTerm)`.

## Estrutura

```
src/rag/
├── settings/configs.py         # Todos os envs (pydantic-settings) + defaults
├── components/                 # Componentes Haystack (Factory .build() ou @component)
│   ├── sources/                # OpenMetadataSource (REST → entidades)           [custom]
│   ├── converters/             # GraphCardBuilder (entidades → cards+nós+arestas) [custom]
│   ├── embedders/              # Ollama (denso) + fastembed/SPLADE (esparso)
│   ├── generators/             # OllamaGenerator (LLM)
│   ├── vectorstore/            # QdrantDocumentStore (híbrido)
│   ├── graphstore/             # Neo4jGraphStore (driver oficial, Cypher)
│   ├── retrievers/             # QdrantHybridRetriever + Neo4jSubgraphRetriever  [custom]
│   └── writers/                # SafeDocumentWriter (Qdrant) + Neo4jGraphWriter  [custom]
└── pipelines/
    ├── ingest/                 # OpenMetadata → grafo (Neo4j) + cards (Qdrant)
    └── query/                  # pergunta → SQL
```

> **Convenção**: pipelines usam só componentes Haystack; todo custom vive em
> `components/`. Factories leem defaults do `.env` (ver
> [configs.py](src/rag/settings/configs.py)).

## MDD do retrieval

Mudanças capazes de alterar ranking, sementes, expansão, poda, cards ou
embeddings seguem desenvolvimento orientado a métricas (*Metric-Driven
Development*). O benchmark termina no subgrafo recuperado, antes do LLM e da
geração de SQL.

- decisão: [ADR-0012](docs/adr/0012-desenvolvimento-orientado-a-metricas-no-retrieval.md);
- contratos, métricas e comandos: [eval/README.md](eval/README.md);
- fluxo para agentes: [skill `evaluate-retrieval-change`](skills/evaluate-retrieval-change/SKILL.md).

```bash
make eval-quick
make eval-full EVAL_TRACE=/caminho/trace.json \
  EVAL_BASELINE=eval/baselines/baseline-aceita.json
```

Enquanto o dataset estiver em `draft`, o trace completo não existir ou não
houver baseline aceita, os resultados são apenas diagnósticos de bootstrap e
não constituem aprovação da qualidade do retrieval.

## Setup

```bash
cp .env.example .env       # ajuste OM_HOST/OM_JWT_TOKEN, modelos e senhas
docker compose up -d       # sobe qdrant, neo4j, redis, hayhooks, webui
ollama pull nomic-embed-text && ollama pull llama3.1
```

Serviços: Hayhooks http://localhost:8000/docs · Qdrant :6333 · Neo4j Browser
:7474 · Open WebUI :3000. O **OpenMetadata é externo** — aponte `OM_HOST` para a
sua instância.

## Uso

```bash
# 1) Ingestão: sincroniza o OpenMetadata → grafo (Neo4j) + cards (Qdrant)
curl -X POST http://localhost:8000/ingest/run \
  -H "Content-Type: application/json" -d '{"with_lineage": true}'

# 2) Query: pergunta em linguagem natural → SQL
curl -X POST http://localhost:8000/query/run \
  -H "Content-Type: application/json" \
  -d '{"query": "faturamento total por mês no último ano"}'
```

## Notas / próximos passos

- **Haystack 3.0** é recém-lançado — rode um smoke-test (`docker compose build`)
  antes de confiar. Os imports das integrações podem ter mudado no major.
- **OpenMetadataSource**: os `fields=` e o parsing seguem a API v1, mas variam por
  versão do OM — confira no Swagger `{OM_HOST}/docs` e ajuste.
- **Qdrant não está pinado** de propósito: fixe `qdrant-client` na versão do seu
  Qdrant de produção (ver [requirements.txt](requirements.txt)).
- Componentes disponíveis mas não plugados nas pipelines atuais: `word_splitter`,
  `qdrant_retriever` (denso puro), `query_to_document`.
- Evolução natural: fechar a query com um **loop** (Haystack 3.0) que executa o
  SQL e reescreve em caso de erro.
