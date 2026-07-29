# Architecture Decision Records (ADRs)

Este diretório guarda as decisões arquiteturais e técnicas do **graphrag-tais**,
o assistente de *text-to-SQL* baseado em GraphRAG sobre os metadados do
OpenMetadata.

## Por que ADRs

O projeto combina várias tecnologias (Haystack, Qdrant, Neo4j, Ollama,
OpenMetadata) e várias decisões de modelagem (o que vira nó, o que vira aresta,
o que é vetorizado, o que valida um JOIN). Um ADR registra o **contexto**, as
**alternativas** e o **racional** de cada decisão, para que qualquer pessoa —
inclusive o próprio autor daqui a seis meses — entenda *por que* o sistema é como
é, e não apenas *o que* ele faz.

## Convenções

- **Numeração**: sequencial, com 4 dígitos. Ex.: `0001-arquitetura-graphrag-openmetadata-text-to-sql.md`.
- **Nome de arquivo**: `NNNN-titulo-em-kebab-case.md`.
- **Imutabilidade**: um ADR aceito não é editado retroativamente para mudar a
  decisão. Se a decisão muda, crie um novo ADR e marque o antigo como
  `Substituído por ADR-XXXX`.
- **Idioma**: português, salvo quando o termo técnico for mais claro em inglês.
- **Template**: todo ADR novo parte de [`template.md`](./template.md).
- **Diagramas**: use `mermaid` (blocos ```` ```mermaid ````), renderizável no
  GitHub e no mkdocs-mermaid2.

## Ciclo de vida (Status)

| Status | Significado |
|---|---|
| `Backlog` | Necessidade de decisão identificada e número reservado, mas ADR ainda não escrito. |
| `Proposto` | Em discussão, ainda não decidido/validado. |
| `Aceito` | Decisão tomada e revisada por pelo menos 1 par. |
| `Rejeitado` | Proposta avaliada e descartada. |
| `Substituído por ADR-XXXX` | Decisão superada por um ADR mais recente. |
| `Depreciado` | Decisão não é mais relevante (contexto desapareceu). |

## Índice

| ADR | Título | Status |
|---|---|---|
| [0000](./0000-escopo-do-projeto-e-mcid-como-base.md) | Escopo do projeto e MCid como base de desenvolvimento | Proposto |
| [0001](./0001-graphrag-como-estrategia-de-recuperacao.md) | GraphRAG como estratégia de recuperação | Proposto |
| [0002](./0002-neo4j-como-banco-de-grafo.md) | Neo4j como banco da camada de grafo | Proposto |
| [0003](./0003-qdrant-como-vector-store.md) | Qdrant como vector store da camada semântica | Proposto |
| [0004](./0004-openmetadata-como-fonte-de-metadados.md) | OpenMetadata como fonte de metadados e ingestão determinística | Proposto |
| [0005](./0005-ingestao-openmetadata-para-neo4j-e-qdrant.md) | Ingestão do OpenMetadata para Neo4j e Qdrant | Substituído por ADR-0011 |
| [0006](./0006-interpretacao-da-pergunta-em-representacao-estruturada.md) | Interpretação da pergunta em uma representação estruturada (IR) | Proposto |
| [0007](./0007-fechamento-dos-gaps-de-correcao-e-consistencia-do-v1.md) | Fechamento dos gaps de correção e consistência do v1 | Proposto |
| [0008](./0008-execucao-do-sql-e-visualizacao-no-v1.md) | Execução do SQL e visualização no v1 (conexão read-only + matplotlib) | Proposto (execução revisada pelo ADR-0013) |
| [0009](./0009-modelo-de-embedding-e-composicao-do-retrieval-hibrido.md) | Modelo de embedding (OpenAI) e composição do retrieval híbrido | Proposto |
| [0010](./0010-camada-de-entrega-tabelas-grafico-explicacao-relatorio.md) | Camada de entrega — tabelas, gráfico, explicação e relatório | Proposto |
| [0011](./0011-contrato-detalhado-da-ingestao-openmetadata-neo4j-qdrant.md) | Contrato detalhado da ingestão OpenMetadata → Neo4j e Qdrant | Proposto |
| [0012](./0012-desenvolvimento-orientado-a-metricas-no-retrieval.md) | Desenvolvimento orientado a métricas no retrieval | Proposto |
| [0013](./0013-execucao-via-postgres-mcp-e-grafico-chart-spec-no-v1.md) | Execução via postgres-mcp com auto-correção e gráfico por chart-spec | Proposto |

### Temas candidatos a ADR (backlog)

Sub-decisões que merecem seu próprio ADR conforme o projeto evolui:

- Modelo detalhado do grafo (labels, propriedades e arestas derivadas `CAN_JOIN`).
- Fusão e ranking da recuperação **híbrida** (RRF, pesos, quotas por tipo e
  reranking).
- Arestas derivadas além do contrato v1, como `CAN_JOIN`.
- **Normalização de valores** (linguagem → valores de domínio) via glossário.
- Validação de **granularidade (grain)** para evitar fan-out no SQL.
- Detecção de **comunidades (Leiden/GDS)** como camada de expansão semântica.
- **Ingestão incremental** de catálogos grandes (a reconciliação de nós órfãos
  entre Neo4j e Qdrant passou a ser coberta pelo [ADR-0007](./0007-fechamento-dos-gaps-de-correcao-e-consistencia-do-v1.md)).
