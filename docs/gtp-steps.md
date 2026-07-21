Sua compreensão está próxima, mas há uma distinção importante: **o algoritmo cria a hierarquia de comunidades; a LLM não precisa criar essa hierarquia**. A LLM pode ser usada depois para interpretar, nomear ou resumir as comunidades.

## O Qdrant e o Neo4j fazem isso sozinhos?

**Não automaticamente.**

* O **Qdrant** armazena os vetores e encontra semanticamente os nós candidatos.
* O **Neo4j** armazena os nós e relacionamentos e executa as travessias.
* O **Neo4j Graph Data Science — GDS** pode executar algoritmos como Leiden.
* Sua aplicação precisa orquestrar quando o algoritmo será executado, em qual projeção do grafo e o que fazer com o resultado.

Inclusive, há uma diferença entre a explicação conceitual e o código daquele tutorial. O texto menciona o agrupamento bottom-up, mas a implementação apresentada **não executa community detection**. Ela faz:

1. busca vetorial no Qdrant;
2. recupera IDs correspondentes no Neo4j;
3. atravessa um ou dois saltos no grafo;
4. formata o subgrafo;
5. entrega esse contexto à LLM.

O `QdrantNeo4jRetriever` apenas interliga a recuperação vetorial aos nós do Neo4j. O código do tutorial usa uma consulta Cypher com até dois hops; não há chamada ao Leiden, Louvain ou outro algoritmo de comunidade. ([qdrant.tech][1])

Portanto, **subir Qdrant + Neo4j não cria um GraphRAG hierárquico automaticamente**.

---

# O que o algoritmo bottom-up realmente faz

No GraphRAG original, a abordagem mais comum é o **Hierarchical Leiden**.

De forma simplificada:

1. cada nó começa em uma comunidade pequena;
2. o algoritmo move e agrupa nós buscando melhorar a modularidade;
3. cada comunidade encontrada é condensada em um “supernó”;
4. o algoritmo repete o processo sobre o grafo condensado;
5. isso gera níveis de agrupamento.

Não são obrigatoriamente três níveis fixos chamados “comunidade, intermediário e global”. O número de níveis depende da topologia, do limite de tamanho, da resolução e das configurações do algoritmo.

O Neo4j descreve Leiden como um algoritmo que separa os nós em comunidades disjuntas, buscando maximizar a modularidade. Ele repete o agrupamento sobre grafos condensados e pode retornar os IDs das comunidades intermediárias. ([Graph Database & Analytics][2])

No Microsoft GraphRAG existe uma etapa adicional: o Hierarchical Leiden é aplicado recursivamente até atingir um limite de tamanho de comunidade. O resultado é uma estrutura estritamente hierárquica, com `parent`, `children`, `level`, entidades e relacionamentos pertencentes à comunidade. ([Microsoft GitHub][3])

Um resultado conceitual poderia ser:

```text
Nível 0 — comunidades locais
├── tabelas de pessoas e beneficiários
├── tabelas de bolsas e pagamentos
└── tabelas territoriais

Nível 1 — áreas intermediárias
├── beneficiários + bolsas
└── território + organizações

Nível 2 — domínio global
└── políticas públicas e concessão de benefícios
```

Esses nomes não são dados pelo Leiden. Ele produz algo semelhante a:

```text
Tabela A -> [comunidade 13, comunidade 7, comunidade 2]
Tabela B -> [comunidade 13, comunidade 7, comunidade 2]
Tabela C -> [comunidade 18, comunidade 7, comunidade 2]
```

Depois você pode gerar nomes e descrições para as comunidades.

---

# Onde entra a LLM

Existem três tarefas diferentes:

### 1. Construção do grafo

No exemplo do Qdrant, uma LLM lê texto e extrai entidades e relacionamentos.

No seu caso, isso é majoritariamente desnecessário, porque o OpenMetadata já entrega dados estruturados:

* tabelas e colunas;
* constraints;
* joins observados;
* data models;
* métricas;
* lineage em nível de tabela;
* lineage em nível de coluna;
* domínios, produtos de dados, glossários e tags.

A própria API de tabelas do OpenMetadata expõe colunas, joins, modelo de dados, métricas, perfis e lineage. O lineage pode ligar diferentes tipos de entidades e preservar direção e profundidade upstream/downstream. ([OpenMetadata Documentation][4])

Então sua ingestão pode ser determinística:

```text
OpenMetadata API
        ↓
Normalização dos metadados
        ↓
Neo4j: nós e relacionamentos
        ↓
Qdrant: cards vetorizados
```

A LLM só deveria complementar aquilo que não está explícito, por exemplo:

* inferir granularidade quando ela não foi documentada;
* classificar uma tabela como fato, dimensão, bridge ou staging;
* sugerir relacionamentos fracos;
* resumir uma comunidade;
* criar sinônimos e descrições;
* interpretar a pergunta do usuário.

### 2. Detecção das comunidades

Aqui **não precisa de LLM**.

O Leiden usa:

* topologia do grafo;
* quantidade de conexões;
* pesos dos relacionamentos;
* parâmetros como resolução;
* tamanho das comunidades;
* número máximo de níveis.

### 3. Resumo das comunidades

Aqui a LLM pode ser útil.

O Microsoft GraphRAG usa uma LLM para produzir, para cada comunidade:

* título;
* resumo;
* descrição;
* principais descobertas;
* ranking de relevância.

Esses relatórios são posteriormente usados principalmente em consultas globais. ([Microsoft GitHub][5])

Para seu primeiro protótipo, você nem precisa começar com LLM nessa etapa. Pode gerar um resumo determinístico:

```text
Comunidade 17

Domínio predominante: concessão de bolsas

Tabelas:
- fct_pagamentos_bolsa
- dim_beneficiario
- dim_programa
- bridge_beneficiario_raca

Métricas:
- total_beneficiarios
- valor_pago
- quantidade_bolsas

Granularidades:
- uma linha por pagamento
- uma linha por beneficiário
- uma linha por programa
```

Depois você usa uma LLM somente para transformar isso em texto mais natural.

---

# O problema dos nós heterogêneos

Seu grafo terá nós muito diferentes:

```text
:Table
:Column
:Metric
:Measure
:Dimension
:Grain
:GlossaryTerm
:Domain
:DataProduct
:Pipeline
```

E relacionamentos como:

```text
(:Table)-[:HAS_COLUMN]->(:Column)

(:Column)-[:FK_TO]->(:Column)

(:Table)-[:LINEAGE_TO]->(:Table)

(:Column)-[:COLUMN_LINEAGE_TO]->(:Column)

(:Table)-[:HAS_GRAIN]->(:Grain)

(:Metric)-[:COMPUTED_FROM]->(:Measure)

(:Measure)-[:USES_COLUMN]->(:Column)

(:Metric)-[:AVAILABLE_BY]->(:Dimension)

(:Table)-[:TAGGED_WITH]->(:GlossaryTerm)

(:Table)-[:BELONGS_TO_DOMAIN]->(:Domain)
```

O Neo4j permite projetar diferentes labels de nós e tipos de relacionamento para o GDS. Entretanto, no caso do Leiden, os relacionamentos selecionados são tratados como conexões de comunidade; ele não entende semanticamente que `FK_TO`, `HAS_COLUMN` e `LINEAGE_TO` têm significados completamente diferentes. Você pode filtrar os tipos e fornecer um peso numérico, mas precisa preparar essa projeção. ([Graph Database & Analytics][6])

Esse é o ponto mais importante para o seu projeto:

> **Não execute Leiden diretamente sobre todos os nós e todas as relações do grafo bruto.**

Caso faça isso, provavelmente ocorrerá algo assim:

* cada tabela ficará fortemente agrupada com suas próprias colunas;
* `HAS_COLUMN` dominará o resultado porque haverá milhares dessas relações;
* tabelas largas terão um peso topológico desproporcional;
* lineage e FK serão tratados como se representassem a mesma afinidade;
* métricas poderão ser arrastadas para comunidades somente por terem muitas dimensões;
* o resultado refletirá a estrutura física, não necessariamente o domínio analítico.

---

# Use dois grafos conceituais

Não significa que você precise de dois bancos Neo4j. Você pode manter um único grafo persistente e criar diferentes **projeções GDS**.

## 1. Grafo operacional completo

Este é usado na recuperação e na construção da consulta:

```text
Table → Column → FK → Column → Table
Metric → Measure → Column
Table → Grain
Table → Lineage → Table
Column → GlossaryTerm
```

Ele mantém:

* direção;
* tipo do relacionamento;
* cardinalidade;
* expressão de join;
* confiança;
* origem da informação;
* contagem de uso;
* lineage;
* granularidade.

## 2. Grafo de afinidade para comunidades

Esse seria uma projeção mais controlada:

```text
Table
Metric
Domain
DataProduct
SemanticModel
```

Eu não colocaria todas as colunas nessa projeção inicial.

Você pode derivar relacionamentos agregados como:

```text
(:Table)-[:COMMUNITY_LINK {
    weight: 0.82,
    fkEvidence: 2,
    lineageEvidence: 1,
    joinUsage: 438,
    sharedTerms: 3
}]->(:Table)
```

O peso pode combinar:

```text
evidência estrutural de FK
+ frequência de joins
+ termos de glossário compartilhados
+ participação nas mesmas métricas
+ proximidade de lineage
+ pertencimento ao mesmo domínio
```

Os valores precisam ser normalizados e avaliados. Não é recomendável simplesmente definir, por exemplo, “FK vale 10 e lineage vale 5” sem testar, porque o volume de cada tipo de relação também influencia o agrupamento.

---

# FK, lineage e afinidade não são a mesma coisa

Essa separação será muito importante na construção de SQL.

## FK

Representa potencial de join estrutural:

```text
orders.customer_id -> customers.id
```

Use para encontrar caminhos de junção.

## Frequently joined

Representa evidência observada:

```text
orders.customer_id -> customers.id
joinCount = 1542
```

O OpenMetadata consegue armazenar relações de colunas frequentemente usadas em joins e suas contagens. Isso pode virar um importante sinal de ranking. ([OpenMetadata Documentation][7])

## Lineage

Representa transformação e proveniência:

```text
raw.pagamentos
    -> stg_pagamentos
    -> fct_pagamentos
```

Isso não significa necessariamente que essas tabelas devem ser unidas em uma consulta.

Lineage pode servir para:

* escolher a tabela analítica mais adequada;
* evitar consultar uma fonte bruta quando existe um mart;
* explicar de onde uma métrica veio;
* encontrar tabelas derivadas;
* verificar se duas tabelas têm uma ancestral comum.

Mas não use `LINEAGE_TO` automaticamente como condição de `JOIN`.

## Termos semânticos

Representam significado compartilhado:

```text
beneficiario_id -> "Beneficiário"
pessoa_id       -> "Beneficiário"
cpf_titular     -> "Beneficiário"
```

Esses relacionamentos ajudam na recuperação semântica, mas não garantem que as colunas sejam diretamente compatíveis.

---

# Como eu modelaria seu grafo

Um núcleo inicial poderia ser:

```text
(:Table {
    id,
    fqn,
    name,
    description,
    tableType,
    layer,
    rowCount
})

(:Column {
    id,
    fqn,
    name,
    description,
    dataType,
    nullable,
    isPrimaryKey
})

(:Grain {
    id,
    description,
    confidence,
    source
})

(:Metric {
    id,
    name,
    description,
    expression,
    defaultTimeGrain
})

(:GlossaryTerm {
    id,
    name,
    description,
    synonyms
})
```

Relacionamentos:

```text
(:Table)-[:HAS_COLUMN]->(:Column)

(:Column)-[:FK_TO {
    cardinality,
    confidence,
    source
}]->(:Column)

(:Column)-[:FREQUENTLY_JOINED_WITH {
    joinCount,
    observationWindow
}]->(:Column)

(:Table)-[:LINEAGE_TO {
    transformationType
}]->(:Table)

(:Column)-[:COLUMN_LINEAGE_TO]->(:Column)

(:Table)-[:HAS_GRAIN]->(:Grain)

(:Grain)-[:IDENTIFIED_BY]->(:Column)

(:Metric)-[:USES_COLUMN]->(:Column)

(:Metric)-[:AVAILABLE_BY]->(:Column)

(:Table)-[:TAGGED_WITH]->(:GlossaryTerm)

(:Column)-[:TAGGED_WITH]->(:GlossaryTerm)
```

Também criaria uma relação derivada em nível de tabela:

```text
(:Table)-[:CAN_JOIN {
    joinCondition,
    cardinality,
    confidence,
    evidence,
    usageCount
}]->(:Table)
```

Ela evita que o sistema tenha sempre que fazer:

```text
Table → Column → FK → Column → Table
```

Você preserva o caminho completo, mas cria uma aresta de conveniência para o SQL planner.

---

# O que vetorizar

Você não precisa vetorizar tudo que existe no Neo4j.

## TableCard

```text
Tipo: tabela
Nome: analytics.fct_pagamentos_bolsa
Descrição: pagamentos efetuados a beneficiários de programas...
Granularidade: uma linha por pagamento por beneficiário
Chaves: pagamento_id
Dimensões relacionadas: beneficiário, programa, território, tempo
Métricas relacionadas: valor pago, quantidade de pagamentos
Camada: analytics
```

## ColumnCard

Inclua o contexto da tabela, porque `status`, `id` e `type` isoladamente são semanticamente inúteis:

```text
Tipo: coluna
Nome: analytics.fct_pagamentos_bolsa.beneficiario_id
Tabela: pagamentos de bolsas
Descrição: identificador da pessoa beneficiária...
Papel semântico: chave estrangeira
Termos: beneficiário, pessoa recebedora
Relaciona-se com: dim_beneficiario.id
```

## MetricCard

```text
Tipo: métrica
Nome: total_beneficiarios
Definição: quantidade distinta de beneficiários...
Expressão: count(distinct beneficiario_id)
Modelo semântico: bolsas
Dimensões disponíveis: raça, sexo, território, programa, período
Granularidade temporal: mês
```

## GrainCard

```text
Tipo: granularidade
Descrição: uma linha por beneficiário por programa por mês
Tabelas compatíveis: ...
Chaves determinantes: beneficiario_id, programa_id, mes
```

## RelationshipCard

Eu só vetoraria relacionamentos quando houver informação semanticamente útil:

```text
Tipo: relacionamento
Origem: fct_pagamentos_bolsa.beneficiario_id
Destino: dim_beneficiario.id
Descrição: associa cada pagamento ao beneficiário recebedor
Cardinalidade: muitos-para-um
Confiança: alta
Origem da evidência: foreign key
```

Uma FK perfeitamente conhecida precisa existir no Neo4j, mas não obrigatoriamente precisa de embedding. O `RelationshipCard` é mais útil para:

* joins inferidos;
* joins com nomes diferentes;
* relações explicadas em linguagem de negócio;
* relações com condições compostas;
* perguntas como “como bolsas se relacionam com raça?”.

Para começar, eu usaria uma coleção Qdrant única, com payload:

```json
{
  "entity_id": "openmetadata-uuid",
  "card_type": "table",
  "fqn": "service.database.schema.table",
  "domain": "bolsas",
  "layer": "analytics"
}
```

E recuperaria quotas diferentes por tipo:

```text
5 tabelas
8 colunas
3 métricas
3 termos de glossário
2 granularidades
```

Assim uma consulta não volta apenas com dezenas de colunas semanticamente parecidas.

---

# Como executar Leiden no Neo4j

Primeiro você cria uma projeção específica para comunidades. Um exemplo conceitual:

```cypher
MATCH (source)-[r:COMMUNITY_LINK]->(target)
WHERE
  (source:Table OR source:Metric OR source:Domain)
  AND
  (target:Table OR target:Metric OR target:Domain)
RETURN gds.graph.project(
  'sql-community-graph',
  source,
  target,
  {
    relationshipProperties: r { .weight }
  },
  {
    undirectedRelationshipTypes: ['*']
  }
);
```

Para agrupamento por afinidade, faz sentido projetar essas relações como não direcionadas, embora você deva preservar as relações direcionadas originais no grafo operacional. A própria documentação do exemplo de Leiden do Neo4j projeta as relações como `UNDIRECTED`, observando que isso funciona melhor para esse algoritmo. ([Graph Database & Analytics][2])

Depois teste com `stream`:

```cypher
CALL gds.leiden.stream('sql-community-graph', {
  relationshipWeightProperty: 'weight',
  includeIntermediateCommunities: true,
  maxLevels: 5,
  gamma: 1.0,
  randomSeed: 42
})
YIELD nodeId, communityId, intermediateCommunityIds
RETURN
  gds.util.asNode(nodeId).fqn AS entity,
  communityId,
  intermediateCommunityIds;
```

O retorno será parecido com:

```text
entity                                  intermediateCommunityIds
analytics.fct_pagamentos                [31, 12, 4]
analytics.dim_beneficiario              [31, 12, 4]
analytics.dim_raca                      [34, 12, 4]
analytics.fct_execucao_orcamentaria     [42, 18, 4]
```

O Neo4j permite retornar ou persistir essas comunidades intermediárias com `includeIntermediateCommunities: true`. Também oferece parâmetros como `maxLevels`, `gamma`, `theta`, pesos e seed para execução incremental. ([Graph Database & Analytics][2])

Somente depois de avaliar o resultado eu usaria `write`:

```cypher
CALL gds.leiden.write('sql-community-graph', {
  writeProperty: 'communityPath',
  relationshipWeightProperty: 'weight',
  includeIntermediateCommunities: true,
  maxLevels: 5,
  randomSeed: 42
})
YIELD communityCount, ranLevels, modularity, modularities;
```

---

# Como isso entra na consulta SQL

Para uma pergunta:

> Quantas mulheres negras recebem bolsas no IPEA?

Eu faria o seguinte.

## 1. Interpretação

Extrair uma representação estruturada:

```json
{
  "operation": "count_distinct",
  "entity": "beneficiários",
  "filters": [
    {"concept": "sexo", "value": "mulher"},
    {"concept": "raça", "value": "negra"}
  ],
  "subject": "bolsas",
  "organization": "IPEA"
}
```

## 2. Recuperação vetorial

Qdrant encontra:

```text
Metric: total_beneficiarios
Table: fct_pagamentos_bolsa
Table: dim_beneficiario
Column: dim_beneficiario.sexo
Column: dim_beneficiario.raca_cor
GlossaryTerm: pessoa negra
GlossaryTerm: beneficiário
```

## 3. Entrada no grafo

Os IDs do Qdrant localizam os mesmos nós no Neo4j. Esse compartilhamento de IDs é justamente o mecanismo de interligação proposto pelo tutorial. ([qdrant.tech][1])

## 4. Travessia controlada

```text
Metric total_beneficiarios
  → USES_COLUMN
  → fct_pagamentos_bolsa.beneficiario_id

fct_pagamentos_bolsa.beneficiario_id
  → FK_TO
  → dim_beneficiario.id

dim_beneficiario
  → HAS_COLUMN
  → sexo

dim_beneficiario
  → HAS_COLUMN
  → raca_cor
```

## 5. Validação de granularidade

O sistema verifica:

```text
fct_pagamentos_bolsa:
uma linha por pagamento

dim_beneficiario:
uma linha por beneficiário
```

Logo, simplesmente fazer `COUNT(*)` poderia contar o mesmo beneficiário várias vezes. O planner seleciona:

```sql
COUNT(DISTINCT beneficiario_id)
```

Essa parte é mais importante para gerar SQL correto do que a comunidade Leiden.

## 6. Comunidades como apoio

A comunidade pode ajudar caso a recuperação inicial seja incompleta.

Por exemplo:

```text
fct_pagamentos_bolsa
  → communityPath [31, 12, 4]
```

Dentro da comunidade 31 podem aparecer:

```text
dim_beneficiario
dim_raca
dim_programa
metric_total_beneficiarios
```

Isso serve como uma expansão adicional, mas não deve substituir a travessia explícita de FK, métrica e granularidade.

---

# Onde as comunidades realmente ajudam no seu caso

Elas são úteis para perguntas mais amplas:

* “Quais dados existem sobre bolsas?”
* “Quais domínios permitem analisar desigualdade racial?”
* “Que conjuntos de dados estão relacionados à execução de políticas públicas?”
* “Quais métricas e dimensões fazem parte do domínio de beneficiários?”
* “Que marts analíticos cobrem orçamento e participação social?”

A busca global do Microsoft GraphRAG usa relatórios de comunidades de determinado nível da hierarquia. Já a busca local começa por entidades semanticamente relacionadas e expande o contexto pelo grafo. ([Microsoft GitHub][8])

Para gerar SQL, a maior parte das consultas será de natureza **local e multi-hop**, e não global.

---

# Minha recomendação para seu primeiro GraphRAG

Eu faria em quatro fases.

## Fase 1 — Sem comunidades

Implemente primeiro:

```text
OpenMetadata
    ↓
Neo4j estruturado
    +
Qdrant com cards
    ↓
recuperação vetorial
    ↓
travessia de FK, métrica e granularidade
    ↓
montagem do contexto SQL
```

Valide se o sistema consegue:

* encontrar tabelas;
* encontrar colunas;
* montar caminhos de join;
* preservar cardinalidade;
* identificar granularidade;
* evitar fan-out;
* localizar métricas e dimensões.

## Fase 2 — Relações derivadas

Adicione:

```text
CAN_JOIN
SAME_BUSINESS_CONCEPT
FREQUENTLY_JOINED_WITH
COMPATIBLE_GRAIN
CAN_FILTER_BY
CAN_GROUP_BY
```

## Fase 3 — Comunidades

Crie uma projeção somente com:

```text
Table
Metric
SemanticModel
Domain
DataProduct
```

Use relações ponderadas e exclua inicialmente `HAS_COLUMN`.

## Fase 4 — Relatórios de comunidade

Gere resumos determinísticos e, depois, use uma LLM para:

* nomear a comunidade;
* produzir uma descrição curta;
* identificar conceitos dominantes;
* resumir tabelas, métricas, dimensões e granularidades.

A arquitetura final ficaria assim:

```text
                         ┌──────────────────────┐
                         │     OpenMetadata     │
                         └──────────┬───────────┘
                                    │
                         ingestão determinística
                                    │
                 ┌──────────────────┴──────────────────┐
                 │                                     │
        ┌────────▼────────┐                   ┌────────▼────────┐
        │     Qdrant      │                   │      Neo4j      │
        │ cards + vetores │                   │ grafo completo  │
        └────────┬────────┘                   └────────┬────────┘
                 │                                     │
          busca semântica                     travessia estrutural
                 │                                     │
                 └──────────────────┬──────────────────┘
                                    │
                         subgrafo candidato
                                    │
                  ┌─────────────────┴──────────────────┐
                  │                                    │
         join/grain validator                community expansion
                  │                                    │
                  └─────────────────┬──────────────────┘
                                    │
                            contexto para SQL
                                    │
                              SQL Builder
```

A principal conclusão é:

> **Para seu caso, Leiden é uma camada auxiliar de organização e expansão semântica. O coração do sistema deve ser a recuperação vetorial seguida de travessias tipadas de FK, lineage, métricas e granularidade.**

Você não precisa mapear as comunidades com LLM. Você precisa **projetar corretamente o grafo sobre o qual Leiden será executado**. A LLM entra antes apenas quando for necessário inferir metadados ausentes, e depois para resumir o resultado.

[1]: https://qdrant.tech/documentation/examples/graphrag-qdrant-neo4j/ "GraphRAG with Qdrant and Neo4j"
[2]: https://www.neo4j.com/docs/graph-data-science/current/algorithms/leiden/ "Leiden - Neo4j Graph Data Science"
[3]: https://microsoft.github.io/graphrag//index/default_dataflow/ "Dataflow - GraphRAG"
[4]: https://docs.open-metadata.org/v1.12.x/api-reference/data-assets/tables "Tables - OpenMetadata Documentation"
[5]: https://microsoft.github.io/graphrag/index/methods/?utm_source=chatgpt.com "Methods - GraphRAG"
[6]: https://neo4j.com/docs/graph-data-science/current/algorithms/leiden/?utm_source=chatgpt.com "Leiden - Neo4j Graph Data Science"
[7]: https://docs.open-metadata.org/v1.12.x/api-reference/data-assets/tables/relationships?utm_source=chatgpt.com "Table Relationships - OpenMetadata Documentation"
[8]: https://microsoft.github.io/graphrag/query/global_search/?utm_source=chatgpt.com "Global Search - GraphRAG"
