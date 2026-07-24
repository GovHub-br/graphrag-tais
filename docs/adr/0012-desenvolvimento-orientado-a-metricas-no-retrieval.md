# ADR-0012: Desenvolvimento orientado a métricas no retrieval

- **Status**: Proposto
- **Data**: 2026-07-24
- **Autores**: arthrok (graphrag-tais)
- **Revisores**: -

## Introdução ao problema

O [ADR-0001](./0001-graphrag-como-estrategia-de-recuperacao.md) separou o
retrieval em entrada semântica e expansão em grafo. O
[ADR-0003](./0003-qdrant-como-vector-store.md) escolheu o Qdrant para a primeira
camada, enquanto o
[ADR-0011](./0011-contrato-detalhado-da-ingestao-openmetadata-neo4j-qdrant.md)
limitou o primeiro contrato útil a `Table`, `Column` e `Relationship`: tabelas
e colunas são recuperáveis como cards, e relacionamentos são arestas que
estruturam a expansão.

A ingestão estabelece o corpus, mas não demonstra que o retrieval encontra as
entidades certas, preserva os caminhos necessários ou evita expandir ruído. A
implementação do retrieval ainda será desenvolvida e decisões como `top_k`,
fusão denso-esparsa, escolha de sementes, profundidade, filtros e poda podem
melhorar uma pergunta enquanto pioram silenciosamente outra. Alterações no
template dos cards ou no perfil de embedding também mudam a recuperação, mesmo
quando acontecem na pipeline de ingestão.

Precisamos definir o processo de avaliação antes dessas escolhas para evitar
que o comportamento corrente, uma demonstração pontual ou uma impressão visual
se torne a referência acidental do projeto.

Nesta ADR, **MDD significa _Metric-Driven Development_ — desenvolvimento
orientado a métricas — e não _Model-Driven Development_**. O princípio é que
toda hipótese capaz de alterar o retrieval deve ser formulada, medida contra um
conjunto versionado e comparada a uma baseline aceita antes de ser concluída.

O objeto avaliado termina antes do LLM:

```text
pergunta
  → recuperação híbrida e ranking de sementes
  → expansão/poda no grafo
  → subgrafo final
  → RetrievalTrace
  ───────────────────────── limite desta ADR
  → LLM / geração e execução de SQL
```

Isso isola falhas de retrieval das falhas de geração. Uma resposta final pode
estar errada apesar de um subgrafo correto, e um LLM pode ocasionalmente
compensar um subgrafo incompleto. Nenhum dos dois casos deve distorcer a
avaliação desta camada.

## Decisão

Adotaremos MDD para todo o retrieval. O diretório `eval/` será a **fonte de
verdade executável** para datasets, contratos, métricas, políticas, baselines e
relatórios. A skill de desenvolvimento apenas orquestrará esse sistema; ela não
implementará métricas, não decidirá limiares, não corrigirá o gold e não
promoverá resultados a baseline.

O ciclo obrigatório de uma mudança será:

1. registrar a hipótese e a configuração que será alterada;
2. executar o perfil `quick` sobre o comportamento anterior;
3. implementar a mudança;
4. executar novamente o perfil `quick` e inspecionar casos e dimensões;
5. executar o perfil `full`;
6. comparar o candidato com a baseline aceita usando a política versionada;
7. concluir a mudança somente com os gates aprovados, ou registrar
   explicitamente a regressão e a decisão humana que a aceita.

O `quick` é feedback de iteração e nunca substitui o `full`. Um resultado sem
baseline compatível pode produzir métricas e servir de candidato inicial, mas
não pode ser chamado de “não regressivo” ou “aprovado”.

### 1. Escopo inicial

Entram no escopo:

- ranking das sementes `Table` e `Column` recuperadas no Qdrant;
- fusão densa e esparsa, filtros, `top_k`, quotas por tipo e reranking;
- seleção de sementes;
- expansão, travessia, ranking e poda no Neo4j;
- nós `Table`/`Column`, arestas `Relationship` e caminhos do subgrafo final;
- separação entre relações de descoberta e relações com
  `sql_join_eligible=true`;
- texto, estrutura, truncamento e versão de `TableCard` e `ColumnCard`;
- modelo, revisão, dimensão, normalização e prefixos do embedding;
- qualquer reindexação ou alteração de corpus que mude o conjunto recuperável;
- contrato do `RetrievalTrace`, gold, métricas, políticas e runner de avaliação.

`Relationship` significa o `RelationshipRecord` e a aresta correspondente do
ADR-0011. No contrato atual, ele não é um nó nem um `RelationshipCard`
independente: é avaliado depois da expansão por suas pontas, tipo, direção,
propriedades e participação em caminhos. Se essa representação mudar no
futuro, a mudança também estará sujeita ao MDD e exigirá versionamento do
contrato.

Ficam fora desta primeira versão:

- escolha, prompt ou avaliação do LLM;
- geração, execução ou correção do SQL final;
- RAGAS ou qualquer LLM-as-a-judge como gate inicial;
- MetricFlow e métricas de negócio;
- glossário;
- granularidade (*grain*) e fan-out;
- lineage de tabela ou coluna;
- qualidade da resposta final e da apresentação.

Esses elementos poderão complementar a avaliação em fases posteriores, mas não
entram no gold nem nos gates deste contrato.

### 2. Gold como subgrafo mínimo suficiente

Cada caso de avaliação representa uma pergunta realista em pt-BR e um
**subgrafo mínimo suficiente** para respondê-la no escopo atual. O gold não é
uma enumeração de tudo que possa ser vagamente relacionado à pergunta. Ele
contém somente:

- tabelas obrigatórias;
- colunas obrigatórias;
- relacionamentos obrigatórios, com tipo e direção;
- pelo menos um caminho completo quando a pergunta depende de mais de uma
  tabela;
- classificação esperada de elegibilidade das relações de JOIN;
- graus de relevância opcionais para avaliar ranking;
- tags de slice, como busca por sigla, FQN, linguagem de negócio, consulta
  mono/multitabela, tipo de relacionamento e quantidade de hops.

O identificador canônico de tabela ou coluna é seu `node_id`/FQN. A identidade
preferencial de uma relação é o `relationship_id` estável; fixtures que não
possuam esse ID devem usar a tupla direcionada
`(relationship_type, source_node_id, target_node_id)`. Inverter as pontas não é
um acerto implícito.

Quando mais de um caminho for correto, o caso deve declarar **alternativas
completas e mutuamente interpretáveis**, e não uma lista solta de arestas que o
avaliador possa combinar arbitrariamente. O avaliador compara o resultado com
cada variante válida, escolhe segundo uma regra determinística a variante de
melhor correspondência e registra qual foi usada. Precisão, excesso e
suficiência são calculados contra a mesma variante; não é permitido usar uma
alternativa para melhorar recall e outra para reduzir ruído no mesmo caso.

Uma estrutura lógica mínima é:

```yaml
case_id: mcid-001
question: "<pergunta em pt-BR>"
scope_id: mcid
tags: [multi_table, acronym, one_hop]
required:
  tables: ["<table_node_id>"]
  columns: ["<column_node_id>"]
  relationships:
    - relationship_id: "<id-estável>"
      relationship_type: SEMANTIC_JOIN
      source_node_id: "<origem>"
      target_node_id: "<destino>"
      sql_join_eligible: true
valid_alternatives:
  - alternative_id: path-a
    additional_tables: []
    additional_columns: []
    relationships: []
    paths:
      - ["<node-a>", "<relationship-id>", "<node-b>"]
relevance:
  "<node_id>": 3
```

O schema físico pode evoluir, mas deve preservar essas semânticas. Um
relacionamento com `sql_join_eligible=false` pode ser gold de descoberta e
aparecer no subgrafo explicativo; ele não pode aparecer no conjunto que o
retriever declara como JOIN autorizado. A avaliação dessa propriedade não
executa nem avalia SQL.

O dataset contém `name`, versão SemVer, `status` (`draft` ou `accepted`) e seus
casos. Apenas dataset `accepted` pode participar de gate. Toda pergunta e todo
subgrafo gold exigem revisão humana por alguém capaz de validar o domínio e o
schema; não serão usados valores de linha, amostras ou PII.

Regras de versão do dataset:

- **MAJOR**: muda a semântica do contrato ou torna os resultados anteriores
  incomparáveis;
- **MINOR**: adiciona casos, novas slices, relações/caminhos alternativos ou
  corrige um gold de forma que possa mudar scores;
- **PATCH**: corrige texto ou metadado sem alterar a pertinência esperada.

Toda revisão registra justificativa e revisor. Gold, policy ou tolerância nunca
são alterados somente para fazer uma implementação passar.

### 3. `RetrievalTrace` como fronteira observável

O retriever deve emitir, para cada caso, um `RetrievalTrace` serializável e
versionado **antes de qualquer chamada ao LLM**. O runner avalia esse contrato,
não objetos internos do pipeline nem texto reconstruído de logs.

O trace deve preservar, no mínimo:

- `trace_schema_version`, `case_id`, `run_id` e `scope_id`;
- pergunta ou referência estável ao caso;
- fingerprint completo da execução;
- sementes ordenadas, com `node_id`, tipo do card, rank, origem do sinal e
  scores disponíveis do denso, esparso, fusão e reranking;
- sementes efetivamente selecionadas para expansão;
- nós finais `Table` e `Column`;
- relacionamentos percorridos e finais, com `relationship_id`, tipo, direção,
  hop e `sql_join_eligible`;
- caminhos finais;
- itens removidos e motivo de poda quando aplicável;
- conjunto de relações declarado como autorizado para JOIN, separado das
  relações usadas apenas para descoberta;
- tempos por estágio em `timings_ms`, tamanho dos conjuntos e erro estruturado,
  se houver.

O trace precisa permitir responder separadamente:

1. o Qdrant encontrou bons pontos de entrada?
2. a expansão acrescentou elementos necessários?
3. os nós corretos estão ligados pelas arestas e caminhos corretos?
4. quanto ruído entrou?
5. alguma relação inelegível foi promovida indevidamente?
6. quanto tempo e quantos elementos foram necessários?

Trace ausente, inválido ou produzido depois do LLM é falha do caso. O contrato
não registra segredo, token, valor de linha nem conteúdo gerado pelo LLM.

### 4. Versões e fingerprints

Todo `RetrievalRun`, relatório e baseline deve registrar informação suficiente
para reprodução e comparação. O fingerprint inclui:

- nome, versão, status e hash de conteúdo do dataset;
- versão do schema de `RetrievalTrace`;
- versão do avaliador e hash da policy;
- revisão do código e indicação de worktree suja;
- configuração do retrieval: `top_k`, filtros, fusão, pesos, quotas,
  reranking, hops e poda;
- `card_schema_version`;
- perfil de embedding: provedor/runtime, modelo, revisão ou digest, dimensão,
  normalização, prefixos/templates e tokenizer;
- collection/alias do Qdrant resolvido para a execução e identificação do
  snapshot do corpus;
- identificação do snapshot do Neo4j e do conjunto de relacionamentos;
- `scope_id`, versões relevantes das dependências e classe do ambiente usada
  para medir latência.

O [ADR-0009](./0009-modelo-de-embedding-e-composicao-do-retrieval-hibrido.md)
e o ADR-0011 tornam modelo, dimensão e template partes da identidade da
indexação. Consequentemente, uma mudança nesses campos exige reindexação e o
fingerprint deve apontar para a collection realmente consultada, não apenas
para um alias mutável.

Há dois conceitos diferentes:

- **compatibilidade da avaliação**: dataset/hash, schemas, avaliador, policy,
  scope e snapshot de dados precisam permitir comparação justa;
- **configuração do sistema candidato**: card, embedding e retrieval podem
  diferir da baseline, pois são justamente as variáveis sob teste.

O runner deve recusar comparações incompatíveis, em vez de produzir uma
diferença numericamente precisa sobre universos distintos.

### 5. Métricas iniciais multidimensionais

Não adotaremos uma média única de “qualidade”. O relatório apresenta métricas
por caso, agregadas e por slices em pelo menos quatro eixos.

#### 5.1 Entrada e ranking

- `SeedTableRecall@K`: cobertura das tabelas gold nas sementes até `K`;
- `SeedColumnRecall@K`: cobertura das colunas gold nas sementes até `K`;
- `SeedNodeRecall@K`: cobertura conjunta de nós;
- `Hit@K`: existência de pelo menos uma semente relevante;
- `MRR@K`: posição da primeira semente relevante;
- `nDCG@K`: qualidade da ordenação quando o caso possuir relevância graduada.

Relacionamentos não entram como seed independente no contrato do ADR-0011.

#### 5.2 Expansão e estrutura

- `ExpansionRecallGain`: diferença de recall entre nós finais e sementes;
- `MarginalExpansionPrecision`: precisão somente dos nós acrescentados pela
  expansão;
- `NodePrecision`, `NodeRecall` e `NodeF1` do subgrafo final;
- `EdgePrecision`, `EdgeRecall` e `EdgeF1`, respeitando tipo e direção;
- `PathRecall`: proporção de caminhos gold integralmente recuperados;
- `StructuralSufficiencyRate`: proporção de casos em que todos os elementos
  obrigatórios e pelo menos uma alternativa completa estão presentes.

`StructuralSufficiencyRate` é binária por caso e não pode ser satisfeita por
nós corretos porém desconectados.

#### 5.3 Minimalidade e segurança

- `OverexpansionRatio`: nós finais irrelevantes em relação ao tamanho do gold;
- `SubgraphSizeRatio`: tamanho do subgrafo recuperado em relação à variante gold;
- `DisconnectedNodeRate`: nós sem conexão útil com o componente recuperado;
- `EligibleRelationshipRecall`: cobertura das relações gold que podem ser
  usadas como JOIN;
- `JoinEligibilityAccuracy`: correção da classificação recuperada;
- `UnauthorizedJoinRate`: relações com `sql_join_eligible=false` incluídas no
  conjunto declarado como autorizado.

`UnauthorizedJoinRate` igual a zero é um invariante, não um termo compensável
por recall. Relações inelegíveis continuam permitidas no conjunto de descoberta.

#### 5.4 Eficiência

- latência total e por estágio, com P50 e P95;
- quantidade de seeds, nós, arestas e caminhos produzidos;
- número de hops e itens podados;
- erros e timeouts por caso.

Latência só participa de comparação bloqueante quando baseline e candidato
usarem a mesma classe de ambiente e protocolo de execução. Nos demais casos,
ela é informativa e essa limitação aparece no relatório.

As métricas são também agregadas por slices definidas no dataset. Um ganho
global não pode esconder, por exemplo, uma regressão em siglas, FQNs, perguntas
multitabela ou `SEMANTIC_JOIN`.

### 6. Runner, perfis e artefatos

O pacote `eval/retrieval/` concentra os modelos, validação, cálculo, comparação
e CLI. Seu entrypoint é:

```text
python -m eval.retrieval.cli validate
python -m eval.retrieval.cli score
python -m eval.retrieval.cli gate
```

Os argumentos e caminhos concretos são documentados em `eval/README.md`. O
contrato lógico contém:

- `RetrievalDataset`: nome, versão, status e casos;
- `RetrievalRun`: dataset, hash, fingerprint e traces;
- `RetrievalTrace`: sementes ordenadas, nós finais, relacionamentos, caminhos,
  tempos, fingerprint e erro;
- `GatePolicy`: invariantes, métricas críticas, direção, tolerâncias e pisos ou
  tetos opcionais;
- relatório: resumo, slices, casos, invariantes, erros e veredito.

O perfil `quick` usa um subconjunto pequeno, estável e estratificado para
feedback local rápido. Ele deve conter pelo menos casos de identificação
lexical, semântica, sigla e relacionamento, sem seleção aleatória a cada run.

O perfil `full` executa o dataset aceito completo e produz o artefato usado pelo
gate. Mudanças em card ou embedding só são avaliadas depois que o corpus
candidato correspondente foi reindexado e identificado no fingerprint.

O runner é responsável por:

- validar schemas e compatibilidade;
- calcular todas as métricas a partir dos IDs do gold e do trace;
- produzir agregados e slices;
- comparar candidato e baseline segundo a policy;
- emitir um veredito reproduzível;
- nunca gravar ou promover uma baseline.

### 7. Policy e gates de não regressão

A policy é configuração versionada em `eval/`, não texto dentro da skill nem
condicional espalhada pelo código. Para cada métrica ela define:

- se é crítica ou informativa;
- direção (`higher_is_better` ou `lower_is_better`);
- tolerância absoluta e/ou relativa contra a baseline;
- piso ou teto absoluto, quando houver;
- slices às quais a regra também se aplica;
- comportamento quando a métrica não puder ser calculada.

Os números não são fixados nesta ADR: serão definidos a partir do dataset
aceito e da primeira baseline revisada, e ficarão explícitos na policy. Isso
evita inventar precisão para um retrieval que ainda não foi medido.

Os gates mínimos são:

1. dataset aceito, schemas válidos e fingerprints completos;
2. todos os casos executados, sem trace ausente ou erro silencioso;
3. IDs sem duplicata, relações com pontas presentes e `scope_id` respeitado;
4. nenhum relacionamento inelegível declarado como JOIN autorizado;
5. nenhuma métrica crítica abaixo do piso/teto absoluto configurado;
6. nenhuma regressão além da tolerância configurada contra a baseline;
7. nenhuma regressão crítica escondida por um agregado quando a policy exigir
   proteção da slice;
8. execução `full` concluída para o candidato.

Não há compensação automática entre dimensões: melhorar MRR não compra uma
queda em `PathRecall`, e mais recall não compra uma violação de elegibilidade.
Uma regressão conscientemente aceita exige justificativa, relatório anexado,
revisor e atualização explícita da baseline ou policy em mudança separada.

### 8. Baseline aceita e bootstrap honesto

Uma baseline é um `RetrievalRun` completo acompanhado de métricas,
fingerprints, dataset/hash, policy, data, revisão e aprovação humana. Commitar
um arquivo de resultado não o torna automaticamente baseline.

No momento desta decisão ainda não existe uma baseline real aceita do
retrieval. Portanto, a criação do scaffold, a validação de fixtures ou um run
sintético **não demonstram qualidade** e não serão registrados como se fossem
uma medição real.

O bootstrap será:

1. criar o dataset em estado `draft`;
2. revisar perguntas e subgrafos gold;
3. versionar e marcar o dataset como `accepted`;
4. executar o perfil `full` sobre o primeiro retrieval reproduzível;
5. gerar um relatório de candidato a baseline;
6. revisar traces, falhas, slices e fingerprints;
7. promover manualmente o resultado a baseline aceita em commit próprio.

Antes do passo 7, `score` pode produzir o relatório, mas `gate` deve permanecer
não aprovado e explicar `no_accepted_baseline` ou incompatibilidade
equivalente. Não existe aprovação automática do primeiro resultado, nem
baseline vazia, perfeita ou fabricada.

### 9. Papel da skill

A skill do projeto orienta humanos e agentes a seguir o ciclo MDD em cada
iteração que afete retrieval. Ela deve:

- identificar se os arquivos alterados estão no escopo desta ADR;
- ler a ADR, a documentação de `eval/` e a policy corrente;
- executar `quick` antes e depois;
- executar `full` antes de concluir;
- chamar os comandos oficiais `validate`, `score` e `gate`;
- apresentar regressões por métrica, slice e caso;
- bloquear a conclusão quando o runner não aprovar ou quando faltar evidência.

Ela não deve:

- recalcular métricas por conta própria;
- embutir thresholds;
- editar gold, policy ou baseline para acomodar a mudança;
- omitir casos com erro;
- promover automaticamente candidato a baseline;
- declarar sucesso a partir de logs, exemplos isolados ou julgamento subjetivo.

Assim, alterar a redação da skill não muda a definição de qualidade. A
autoridade permanece nos contratos e artefatos versionados em `eval/`.

## Alternativas consideradas

### Alternativa A: avaliar manualmente algumas perguntas

- **Prós**: início rápido e pouco código.
- **Contras**: baixa repetibilidade, viés de seleção e ausência de histórico;
  resultados visualmente plausíveis não revelam perda de caminho ou excesso de
  expansão.
- **Decisão**: rejeitada como gate; continua útil somente para investigação.

### Alternativa B: usar apenas métricas tradicionais de RAG ou RAGAS

- **Prós**: ferramentas e métricas conhecidas para contexto textual.
- **Contras**: uma lista com os nós corretos pode receber bom score mesmo sem
  arestas, direção, conectividade ou caminho válido. Métricas com juiz LLM
  também introduzem custo e não determinismo desnecessários neste estágio.
- **Decisão**: rejeitada como gate inicial. Pode ser adicionada futuramente
  como avaliação complementar, sem substituir as métricas estruturais.

### Alternativa C: avaliar apenas o SQL ou a resposta final

- **Prós**: mede um resultado próximo do usuário.
- **Contras**: mistura retrieval, LLM, execução e apresentação; dificulta
  localizar a causa de uma regressão e permite compensações acidentais entre
  camadas.
- **Decisão**: rejeitada para esta ADR; avaliação ponta a ponta será
  complementar no futuro.

### Alternativa D: usar um score composto único

- **Prós**: comparação e ranking simples.
- **Contras**: pesos arbitrários permitem que recall, ranking ou latência
  escondam perda estrutural e violações de segurança.
- **Decisão**: rejeitada. Os gates permanecem multidimensionais.

### Alternativa E: implementar avaliação dentro da skill

- **Prós**: menos estrutura aparente no repositório.
- **Contras**: lógica difícil de testar, versionar e executar fora de um agente;
  mudanças de prompt da skill alterariam o julgamento.
- **Decisão**: rejeitada. A skill é apenas orquestração.

### Não fazer nada / manter o status quo

- **Descrição**: desenvolver retrieval por percepção individual e criar
  benchmarks depois.
- **Por que não**: consolida escolhas sem evidência, não cria baseline anterior
  às features e torna regressões futuras difíceis de atribuir.

## Tradeoffs

### Vantagens

- **[Alto impacto]** Cada decisão de retrieval passa a ter evidência
  reproduzível antes de ser concluída.
- **[Alto impacto]** O `RetrievalTrace` separa defeitos de sementes, expansão,
  estrutura e elegibilidade sem depender do LLM.
- **[Alto impacto]** Gold de subgrafo mede relações e caminhos, não apenas
  similaridade textual.
- **[Médio impacto]** Versionamento e fingerprints tornam comparáveis mudanças
  de card, embedding e configuração.
- **[Médio impacto]** Perfis `quick` e `full` equilibram velocidade de iteração e
  confiança.
- **[Médio impacto]** A separação runner/skill permite uso igual por pessoas,
  CI e agentes.

### Desvantagens

- **[Alto impacto]** Curar subgrafos mínimos, caminhos alternativos e
  elegibilidade exige tempo de especialistas.
- **[Médio impacto]** O runner, traces e artefatos acrescentam código e
  manutenção antes da primeira feature de retrieval.
- **[Médio impacto]** Mudanças no corpus ou embedding exigem reindexação e
  controle rigoroso de fingerprints.
- **[Médio impacto]** Gates podem retardar experimentos e ainda dependem de um
  dataset representativo.
- **[Baixo impacto]** Métricas de latência podem ser ruidosas fora de ambiente
  controlado.

### Avaliação

Os ganhos superam os custos. O retrieval é a fundação estrutural do GraphRAG e
será desenvolvido por mais de uma pessoa; estabelecer MDD agora é mais barato
do que reconstruir depois quais mudanças causaram cada regressão. O principal
risco ativo é transformar um gold pequeno em falsa segurança. Ele será mitigado
por revisão humana, SemVer, slices explícitas, crescimento controlado do
dataset e pela proibição de tratar o bootstrap como baseline real.

## Consequências

- **Positivas**:
  - cada mudança de retrieval produz trace, métricas e veredito auditáveis;
  - falhas vetoriais, estruturais e de elegibilidade podem ser diagnosticadas
    separadamente;
  - cards e embeddings deixam de ser escolhidos apenas por intuição;
  - novos desenvolvedores recebem um processo único desde a primeira feature.
- **Negativas**:
  - há custo inicial de infraestrutura de avaliação e curadoria;
  - o time precisa manter dataset, policy e baseline com o mesmo rigor do
    código;
  - não haverá alegação de não regressão até existir uma baseline real aceita.
- **Ações decorrentes**:
  - criar `eval/retrieval/`, schemas, CLI e documentação;
  - criar datasets `quick` e `full` inicialmente em estado honesto de
    bootstrap;
  - instrumentar o retrieval para emitir `RetrievalTrace` pré-LLM;
  - curar e revisar o primeiro conjunto gold de subgrafos mínimos;
  - executar e aprovar manualmente a primeira baseline real;
  - criar a policy inicial sem inventar valores antes das medições;
  - instalar a skill do projeto e exigir seu uso em mudanças de retrieval;
  - integrar o perfil `full` e o gate à CI quando houver baseline aceita.

## Referências

- [ADR-0001: GraphRAG como estratégia de recuperação](./0001-graphrag-como-estrategia-de-recuperacao.md)
- [ADR-0003: Qdrant como vector store](./0003-qdrant-como-vector-store.md)
- [ADR-0004: OpenMetadata como fonte de metadados](./0004-openmetadata-como-fonte-de-metadados.md)
- [ADR-0009: Modelo de embedding e retrieval híbrido](./0009-modelo-de-embedding-e-composicao-do-retrieval-hibrido.md)
- [ADR-0011: Contrato detalhado da ingestão OpenMetadata → Neo4j e Qdrant](./0011-contrato-detalhado-da-ingestao-openmetadata-neo4j-qdrant.md)
- [Notas de avaliação GraphRAG e MDD](../../mdd.md)
