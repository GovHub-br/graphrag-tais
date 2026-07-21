# ADR-0002: Neo4j como banco da camada de grafo

- **Status**: Proposto
- **Data**: 2026-07-21
- **Autores**: arthrok (graphrag-tais)
- **Revisores**: -

## Introdução ao problema

O [ADR-0001](./0001-graphrag-como-estrategia-de-recuperacao.md) definiu que a
recuperação tem uma **camada de grafo**, responsável pela **travessia tipada de
relações** (FK, lineage, métrica, granularidade). Falta escolher a tecnologia que
implementa essa camada.

Os requisitos dessa camada são específicos:

- **Travessia multi-hop eficiente e expressiva**: seguir `Column -[:FK_TO]->
  Column <-[:HAS_COLUMN]- Table` em 1–3 saltos é a operação mais frequente.
- **Relações tipadas com propriedades** (cardinalidade, confiança, joinCount).
- **On-prem** (LGPD; o schema não sai da infra).
- Idealmente, espaço para **algoritmos de grafo** (community detection / Leiden)
  numa fase futura, sem trocar de tecnologia.

## Decisão

Adotamos o **Neo4j** como banco da camada de grafo, acessado por **Cypher**.

- É um **property graph** nativo: nós e arestas tipadas com propriedades, modelo
  que espelha 1:1 o que precisamos (`FK_TO`, `LINEAGE_TO`, `HAS_GRAIN`, …).
- **Cypher** expressa travessias multi-hop de forma direta e legível, que é a
  operação central da recuperação.
- O **Neo4j Graph Data Science (GDS)** oferece **Leiden** e afins — deixando a
  porta aberta para a fase de comunidades **sem trocar de banco**.
- Roda **on-prem** via container (já previsto no `docker-compose`).

Decisão correlata de integração: usaremos o **driver oficial `neo4j`** (Cypher
puro), **não** a integração `neo4j-haystack`. Aquela integração trata o Neo4j como
um *vector store* (busca vetorial dentro do Neo4j), o que é o oposto do nosso
desenho — aqui o Neo4j é **grafo puro**, e a busca vetorial vive no vector store
(ADR-0003). Usar o driver evita uma dependência desalinhada e nos dá Cypher
irrestrito para a travessia.

## Alternativas consideradas

### Alternativa A: Grafo emulado em PostgreSQL (FK + CTEs recursivas)

- Descrição: modelar as relações em tabelas relacionais e percorrer com
  `WITH RECURSIVE`.
- Prós: um banco a menos se já houvesse Postgres; SQL familiar.
- Contras: travessia multi-hop com CTE recursiva é verbosa e cara; sem algoritmos
  de comunidade; o modelo não expressa naturalmente relações tipadas com
  propriedades. Fricção alta justamente na operação mais comum.

### Alternativa B: Outro banco de grafo (Memgraph, ArangoDB, Amazon Neptune)

- Descrição: property/multi-model graphs alternativos.
- Prós: Memgraph fala Cypher e é rápido; ArangoDB é multi-model; Neptune é gerenciado.
- Contras: Neptune é AWS-gerenciado (contra on-prem/LGPD). Memgraph/ArangoDB têm
  ecossistema e maturidade de algoritmos de grafo menores que o do Neo4j+GDS, que
  é referência para Leiden — algoritmo que queremos na fase de comunidades.

### Alternativa C: Triplestore RDF/SPARQL

- Descrição: modelar como triplas e consultar em SPARQL.
- Prós: padrão semântico forte, ontologias.
- Contras: property graph + Cypher é mais ergonômico para *joins de schema* com
  propriedades nas arestas; SPARQL adiciona complexidade sem ganho para o caso.

### Não fazer nada

- Descrição: manter só a camada vetorial, sem grafo.
- Por que não: contraria o ADR-0001; sem grafo não há travessia tipada.

## Tradeoffs

### Vantagens

- **[Alto impacto]** Modelo e linguagem (property graph + Cypher) casam com a
  operação central (travessia tipada multi-hop).
- **[Médio impacto]** GDS/Leiden disponível para a evolução de comunidades sem
  troca de tecnologia.
- **[Médio impacto]** Ecossistema maduro, Neo4j Browser para inspeção visual do
  grafo — útil no desenvolvimento e na depuração.

### Desvantagens

- **[Alto impacto]** É **mais um banco** para operar, versionar e sincronizar com
  o vector store.
- **[Médio impacto]** **Licenciamento**: a Community Edition atende o núcleo, mas
  há limites (ex.: um database ativo; recursos avançados de GDS/cluster são
  Enterprise). Precisamos validar que a fase de comunidades cabe no que é gratuito.
- **[Baixo impacto]** Curva de Cypher para quem vem só de SQL.

### Avaliação

Os ganhos superam os custos: a camada de grafo é definidora do projeto e o
Neo4j+Cypher é a opção de menor fricção para travessia tipada, com caminho de
evolução (GDS) já garantido. O risco ativo a acompanhar é o **licenciamento** dos
recursos avançados (GDS/Enterprise) — a validar antes da fase de comunidades.

## Consequências

- **Positivas**: camada de grafo com modelo natural e caminho claro para
  algoritmos de comunidade.
- **Negativas**: custo operacional de um banco adicional; dependência do
  ecossistema Neo4j; verificar limites da Community Edition.
- **Ações decorrentes**:
  - Provisionar Neo4j no `docker-compose` (feito).
  - Implementar acesso via driver oficial (componentes `Neo4jGraphStore`,
    `Neo4jGraphWriter`, `Neo4jSubgraphRetriever`).
  - Validar limites de licença antes de habilitar GDS/Leiden (fase futura).

## Referências

- [ADR-0001: GraphRAG como estratégia de recuperação](./0001-graphrag-como-estrategia-de-recuperacao.md)
- [Neo4j Graph Data Science — Leiden](https://neo4j.com/docs/graph-data-science/current/algorithms/leiden/)
- [Cypher Query Language](https://neo4j.com/docs/cypher-manual/current/)
