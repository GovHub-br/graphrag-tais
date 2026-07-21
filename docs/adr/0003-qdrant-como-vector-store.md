# ADR-0003: Qdrant como vector store da camada semântica

- **Status**: Proposto
- **Data**: 2026-07-21
- **Autores**: arthrok (graphrag-tais)
- **Revisores**: -

## Introdução ao problema

O [ADR-0001](./0001-graphrag-como-estrategia-de-recuperacao.md) definiu que a
recuperação tem uma **camada semântica**, cujo papel é a **entrada**: traduzir a
pergunta em linguagem natural nas **entidades candidatas** (tabelas, colunas,
métricas) que servem de semente para a travessia no grafo (ADR-0002). Falta
escolher a tecnologia dessa camada.

Requisitos:

- **Busca vetorial** por similaridade sobre *cards* (uma descrição por entidade).
- **Payload/metadados filtráveis** — cada ponto guarda o `node_id` que costura o
  vetor ao nó correspondente no grafo, além de `card_type`, `fqn`, etc.
- **On-prem** (LGPD).
- Suporte a **vetores densos e esparsos** no mesmo ponto — para uma fase de
  recuperação híbrida (identificador exato + semântica).
- Integração com **Haystack**.

## Decisão

Adotamos o **Qdrant** como vector store da camada semântica.

- Busca vetorial performática com **filtro por payload** rico — essencial para o
  `node_id` (a costura com o Neo4j) e para *quotas por tipo* de card.
- Suporte nativo a **vetores esparsos além dos densos** no mesmo ponto, o que
  habilita recuperação **híbrida** (decisão de *como usar* fica para ADR próprio).
- Roda **on-prem** via container (já no `docker-compose`).
- Integração madura com Haystack (`qdrant-haystack`).
- **Já é usado em produção** no nosso contexto — reduz risco operacional e
  reaproveita conhecimento de operação existente.

## Alternativas consideradas

### Alternativa A: pgvector (extensão do PostgreSQL)

- Descrição: guardar embeddings numa coluna vetorial do Postgres.
- Prós: um banco a menos se já houvesse Postgres; SQL familiar.
- Contras: recursos de filtragem/híbrido e desempenho de ANN em escala ficam atrás
  de um vector store dedicado; suporte a esparso/híbrido é mais limitado.

### Alternativa B: Índice vetorial do próprio Neo4j

- Descrição: usar o vector index do Neo4j e ter **um banco só**.
- Prós: elimina a sincronização entre dois stores.
- Contras: acopla busca semântica e grafo num só motor, contrariando a separação
  de camadas do ADR-0001; filtragem/híbrido e tuning de recuperação são menos
  ricos que no Qdrant; sobrecarrega o Neo4j com uma responsabilidade que não é a
  dele. (Foi justamente por isso que descartamos `neo4j-haystack` no ADR-0002.)

### Alternativa C: Outro vector store (Weaviate, Milvus, Elasticsearch/OpenSearch kNN)

- Descrição: outros motores de busca vetorial.
- Prós: todos capazes e on-prem.
- Contras: nenhum ganho decisivo sobre o Qdrant para o caso, e **nenhum já está em
  produção conosco** — adotá-los adicionaria risco operacional sem contrapartida.

### Não fazer nada

- Descrição: só grafo, sem camada semântica.
- Por que não: contraria o ADR-0001; a entrada por linguagem natural ambígua fica
  frágil.

## Tradeoffs

### Vantagens

- **[Alto impacto]** Payload filtrável + esparso/denso atendem exatamente a costura
  com o grafo (`node_id`) e a futura recuperação híbrida.
- **[Médio impacto]** **Já em produção** no nosso contexto: menor risco, operação
  conhecida.
- **[Médio impacto]** Integração Haystack pronta e boa performance de ANN.

### Desvantagens

- **[Alto impacto]** É **mais um banco** a operar e a manter sincronizado com o
  Neo4j (os dois recebem a mesma ingestão).
- **[Baixo impacto]** Compatibilidade de versão **cliente × servidor**: um
  `qdrant-client` novo demais pode usar APIs que o servidor de produção não expõe
  — por isso o `qdrant-client` é mantido **sem pin fixo** nas dependências, para
  casar com a versão do Qdrant de produção.

### Avaliação

Os ganhos superam os custos: o Qdrant atende todos os requisitos da camada
semântica e, decisivamente, **já está em produção**, o que minimiza risco. O custo
principal (sincronização com o Neo4j) é inerente à arquitetura de duas camadas do
ADR-0001, não específico do Qdrant. Risco ativo: alinhar a versão do client à do
servidor de produção.

## Consequências

- **Positivas**: camada semântica sobre tecnologia conhecida, com filtro por
  payload e caminho para híbrido.
- **Negativas**: sincronização com o grafo; atenção à compatibilidade de versão.
- **Ações decorrentes**:
  - Provisionar Qdrant no `docker-compose` (feito).
  - Manter `qdrant-client`/`qdrant-haystack` **sem pin** até fixar a versão do
    Qdrant de produção.
  - Registrar em ADR próprio a decisão de **recuperação híbrida (denso+esparso)** —
    *como* o Qdrant é consultado.

## Referências

- [ADR-0001: GraphRAG como estratégia de recuperação](./0001-graphrag-como-estrategia-de-recuperacao.md)
- [ADR-0002: Neo4j como banco da camada de grafo](./0002-neo4j-como-banco-de-grafo.md)
- [Qdrant — Documentação](https://qdrant.tech/documentation/)
- [Qdrant — Hybrid Queries (dense + sparse)](https://qdrant.tech/documentation/concepts/hybrid-queries/)
