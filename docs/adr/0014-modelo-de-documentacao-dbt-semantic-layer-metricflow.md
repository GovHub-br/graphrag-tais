# ADR-0014: Modelo de documentação para dbt Semantic Layer + MetricFlow

- **Status**: Proposto
- **Data**: 2026-07-27
- **Autores**: Lucas Bottino (graphrag-tais)
- **Revisores**: -

> **Nota de escopo.** Este ADR fixa o **modelo de documentação** (o que cada
> artefato descreve, com que convenção, onde vive) que alimenta tanto a
> ingestão via OpenMetadata quanto o consumo direto do dbt Semantic
> Layer/MetricFlow. **Não decide qual das duas rotas de integração
> prevalece** — o time está testando as duas em paralelo nesta fase, e o
> modelo aqui definido é o insumo comum a ambas, para não duplicar trabalho
> de curadoria enquanto essa decisão amadurece.

## Introdução ao problema

O [ADR-0006](./0006-interpretacao-da-pergunta-em-representacao-estruturada.md)
definiu que a IR fala em **conceitos de negócio** (`concept`, `metric`), não em
nomes físicos de schema — mas hoje esses campos não resolvem contra nada real:
são texto que o LLM preenche e ninguém valida (o próprio estado atual do
projeto registra isso como gap: a IR "ainda não reescreve o roteamento da
recuperação por conceito"). O
[ADR-0011](./0011-contrato-detalhado-da-ingestao-openmetadata-neo4j-qdrant.md)
modela o contrato de ingestão até `Table`/`Column`/`Relationship` e **exclui
explicitamente** deste escopo "MetricFlow, métricas... granularidade (*grain*)
e validação de *fan-out*... glossário". O
[ADR-0012](./0012-desenvolvimento-orientado-a-metricas-no-retrieval.md)
exclui o mesmo do escopo do *eval*. E o *grain*/*fan-out* é citado
repetidamente ([ADR-0001](./0001-graphrag-como-estrategia-de-recuperacao.md),
[ADR-0007](./0007-fechamento-dos-gaps-de-correcao-e-consistencia-do-v1.md),
[ADR-0010](./0010-camada-de-entrega-tabelas-grafico-explicacao-relatorio.md))
como o maior risco de correção semântica ainda sem solução.

A equipe responsável pela documentação das tabelas do MCid vai adotar **dbt
Semantic Layer + MetricFlow** — decisão de produto já tomada, fora do escopo
deste ADR. Falta decidir **o modelo**: o que cada artefato documenta, com que
convenção de nomenclatura, onde vive no projeto dbt, e como isso mapeia para o
contrato que o graphrag-tais já tem — para que sirva de insumo às duas rotas de
integração (alimentar o OpenMetadata **e** ser consumido diretamente) sem
retrabalho de curadoria.

**Levantamento em `data-application-cidades`** (repositório do MCid): a
documentação de tabela/coluna já existe e é madura — `schema.yml` com
`description` em tabela e coluna, `tags`, testes de qualidade (`row_count_match`).
Não há **nenhum** artefato de Semantic Layer/MetricFlow ainda — é greenfield.
O publicador hoje referenciado pelo ADR-0011 para `mcidSemanticRelationships`
não foi localizado nesse repositório (busca exaustiva em todo o histórico e
todas as branches); está **pendente de confirmação pelo time** antes de
desenhar o adapter da Rota A em detalhe.

## Decisão

Adotamos um modelo de documentação em **5 camadas**, complementares às
camadas 0 (tabela/coluna) que já existem hoje. As camadas 1–4 mapeiam
diretamente para as construções nativas do dbt Semantic Layer/MetricFlow
(`entities`, `dimensions`, `measures`, `metrics`); a camada 5 (glossário) é
transversal.

| Camada | Construção dbt | O que resolve | Mapeamento no graphrag-tais |
|---|---|---|---|
| **0. Tabela/Coluna** | `schema.yml` — `description`, `tags` (já existe) | Descrição documental básica | `TableRecord.description` / `ColumnRecord.description` (ADR-0011) — sem mudança |
| **1. Entidades** | `semantic_models[].entities` | Substitui o parser Markdown frágil (`mcid_markdown_v1`) por chave/grain estruturada e testável (`dbt build` valida) | Novo adapter `dbt_semantic_manifest_v1` → `RelationshipRecord`, via a interface `RelationshipSourceAdapter` já prevista no ADR-0011 |
| **2. Dimensões** | `semantic_models[].dimensions` | Dá ao `concept`/`group_by` da IR (ADR-0006) um alvo real, resolvível, em vez de busca vetorial cega no nome da coluna | Enriquece `ColumnRecord` (`semantic_role: dimension`, `dimension_type: categorical\|time`) |
| **3. Medidas** | `semantic_models[].measures` | Resolve *grain*/*fan-out*: toda medida declara `agg`, e o MetricFlow expande por entidade antes de agregar | Enriquece `ColumnRecord` (`semantic_role: measure`, `agg`) |
| **4. Métricas** | `metrics.yml` | Dá ao campo `metric` da IR uma lista finita e versionada, em vez do LLM inventar um nome | Tipo novo `MetricRecord` → nó `:Metric` + `MetricCard` (mesmo padrão de `TableCard`/`ColumnCard`) |
| **5. Glossário** | `meta.synonyms` em entidades/dimensões, ou `GlossaryTerm` do OM | Resolve linguagem natural → valor de domínio (ex.: "mulheres negras" → `sexo=feminino AND raca IN (preta,parda)`), hoje assumido pelo ADR-0006 e nunca implementado | `Glossary` do OpenMetadata (endpoint hoje fora do escopo do ADR-0011 — precisará entrar) |

### 1. Onde cada camada vive (convenção de arquivo)

- Camada 0: `schema.yml`, como já é feito — sem mudança.
- Camadas 1–3 (`entities`/`dimensions`/`measures`): em `semantic_models.yml`,
  **colocado ao lado do `schema.yml` da camada gold** do subject area (nunca em
  bronze — dado bruto/não tipado não é base semântica válida; se não existir
  gold para o modelo, usar silver).
- Camada 4 (`metrics`): em `metrics.yml`, um por subject area (pode referenciar
  medidas de mais de um `semantic_model` do mesmo subject area).
- Camada 5 (glossário): entradas curtas em `meta.synonyms` na própria
  entidade/dimensão quando o sinônimo for local a uma tabela; termos amplos
  (siglas do domínio, categorias de negócio) vão para o `Glossary` nativo do
  OpenMetadata, para serem reutilizáveis entre subject areas e projetos GovHub.

### 2. Convenção de nomenclatura e checklist obrigatório

**Regras gerais (todas as camadas 1–4):**

- Nome de **negócio**, não físico ou de implementação: `total_financiamento_far`,
  nunca `sum_valor_far_query3`.
- `snake_case`, em português, sem abreviação não documentada — se abreviar
  (`far`, `apf`, `uh`), a sigla precisa de entrada no glossário.
- `description` nunca vazia: mínimo uma frase em linguagem de negócio, que não
  repita o nome do campo.
- Sem colisão de nome entre camadas: uma `dimension` não pode ter o mesmo nome
  de uma `metric` — isso quebra a resolução determinística do `concept`/`metric`
  da IR.

**Checklist — Entidade (`entities`):**
- [ ] `name` é o nome de negócio da chave (`apf`), não um identificador técnico (`id_1`).
- [ ] `type` declarado: `primary` (grain desta tabela) | `foreign` | `unique`.
- [ ] `expr` aponta para a coluna física real.
- [ ] Se a chave exige normalização (ex.: remover máscara de CNPJ), isso é
      explícito em `expr` — nunca implícito ou documentado só em prosa.
- [ ] Toda tabela tem **exatamente uma** entidade `primary` — grain ambíguo
      quebra a expansão automática do MetricFlow.

**Checklist — Dimensão (`dimensions`):**
- [ ] `type` sempre declarado: `categorical` ou `time` (nunca ausente).
- [ ] Se `time`, `time_granularity` declarado.
- [ ] `description` explica o domínio de valores quando não é óbvio (ex.:
      códigos `1`/`2`/`3`).
- [ ] Sinônimos de negócio ("UF" = "estado") registrados em `meta.synonyms`.

**Checklist — Medida (`measures`):**
- [ ] `agg` sempre explícito — nunca herdar um default implícito.
- [ ] `description` diz o que está sendo somado/contado e a unidade (R$,
      unidades, %).
- [ ] Contagem de entidade usa `count_distinct` na entidade, nunca `count(*)`
      (fan-out silencioso).

**Checklist — Métrica (`metrics`):**
- [ ] `label` em português natural — é o que aparece para quem consome.
- [ ] `description` inclui a régua de negócio (período padrão, se aplicável).
- [ ] Toda métrica referencia uma medida **existente e documentada** — nunca
      uma expressão solta sem lastro em `measures`.

**Checklist — Glossário:**
- [ ] Toda sigla/abreviação usada em nome de tabela, coluna, entidade ou
      dimensão tem entrada de glossário, mesmo que curta.
- [ ] Sinônimos de linguagem natural usados em perguntas reais são registrados
      como termo de glossário — não ficam para o LLM inferir sozinho.

### 3. Responsabilidade (ownership)

- **Camada 0**: quem já mantém o dbt hoje (equipe de dados do MCid) — sem
  mudança.
- **Camadas 1–4**: a mesma equipe autora, mas com **revisão cruzada** de quem
  consome (BI/Superset e graphrag-tais) antes de aceitar — erro aqui quebra
  correção semântica de forma silenciosa (grain errado não dá erro, dá número
  errado).
- **Camada 5**: pode ser compartilhada com áreas de negócio (não só dados), por
  ser a camada menos técnica.

### Exemplo aplicado

Hoje (`schema.yml`, camada 0 — já existe em `empreendimento_far_dbt/bronze`):

```yaml
- name: cadastro_pj
  description: >
    Cadastro detalhado de pessoa jurídica (empreendimento contratado).
    822 empreendimentos com composição financeira completa...
  columns:
    - name: apf
      description: APF normalizado para 8 dígitos.
```

Adicionado (`semantic_models.yml`, camadas 1–3):

```yaml
semantic_models:
  - name: cadastro_pj
    model: ref('cadastro_pj')
    description: "Cadastro de PJ (empreendimento contratado) do FAR."
    entities:
      - name: apf
        type: primary
        expr: apf
    dimensions:
      - name: uf
        type: categorical
      - name: data_contratacao
        type: time
        type_params: {time_granularity: day}
    measures:
      - name: valor_far
        agg: sum
        description: "Valor total do empréstimo FAR, em R$."
      - name: qtd_empreendimentos
        agg: count_distinct
        expr: apf
        description: "Quantidade de empreendimentos distintos (por APF)."
```

Adicionado (`metrics.yml`, camada 4):

```yaml
metrics:
  - name: total_financiamento_far
    type: simple
    label: "Total de financiamento FAR"
    description: "Soma do valor FAR contratado no período."
    type_params: {measure: valor_far}
```

Com isso, a IR `{"metric": "total_financiamento_far", "filters": [{"concept": "uf", "value": "DF"}]}`
deixa de ser um palpite do LLM sobre nome de coluna e passa a ser um lookup
contra um manifesto real — e a entidade `apf` garante grain correto se a
pergunta precisar juntar `cadastro_pj` com outra tabela também ancorada em
`apf` (ex.: `consolidado`).

## Alternativas consideradas

### Alternativa A: Documentar só no OpenMetadata (Custom Properties / Glossary nativo), sem dbt Semantic Layer

- Descrição: manter tudo centralizado no OM, sem introduzir `entities`/
  `dimensions`/`measures`/`metrics` do dbt.
- Contras: sem MetricFlow não há validação estrutural (`dbt build`) de
  entidade/grain — mesmo problema do parser Markdown atual, só que também sem
  o cálculo de SQL grain-safe que o MetricFlow oferece; métricas ficariam soltas
  como texto, sem lastro determinístico.
- Rejeitada como única fonte, mas o OM segue como destino da Rota A.

### Alternativa B: Documentar tudo dentro do próprio graphrag-tais (glossário/métricas definidos no repo do RAG)

- Descrição: o time do RAG cura entidades/dimensões/métricas diretamente no
  graphrag-tais.
- Contras: quem sabe a semântica de negócio é o time de dados do MCid, não o
  time do RAG; duplica manutenção; contraria o princípio que já funciona hoje
  no `schema.yml` ("quem gera o dado documenta o dado").
- Rejeitada.

### Alternativa C: Um único arquivo YAML por schema, em vez de arquivo por camada

- Descrição: misturar `schema.yml` + `semantic_models` + `metrics` num arquivo
  só por schema.
- Contras: o dbt Semantic Layer já separa naturalmente esses artefatos;
  misturar tudo dificulta revisão incremental e aumenta conflito de merge
  entre autores diferentes (quem documenta coluna não é sempre quem documenta
  métrica).
- Rejeitada.

### Não fazer nada / manter status quo (só `schema.yml`)

- Descrição: seguir documentando só tabela/coluna, como hoje.
- Por que não: é exatamente o gap que motivou esta decisão — sem isso,
  `concept`/`metric` da IR nunca resolvem contra nada real, e *grain*/*fan-out*
  seguem sem solução estrutural.

## Tradeoffs

### Vantagens

- **[Alto impacto]** Substitui o parser Markdown frágil (`mcid_markdown_v1`)
  por um contrato testável e estruturado — o próprio `dbt build` valida
  entidades e medidas antes de chegar ao graphrag-tais.
- **[Alto impacto]** Dá lastro real ao `concept`/`metric` da IR (ADR-0006), que
  hoje é texto solto sem validação.
- **[Alto impacto]** Ataca diretamente o gap de *grain*/*fan-out*, citado como
  risco não mitigado em quatro ADRs anteriores (0001, 0007, 0010, 0011).
- **[Médio impacto]** Reaproveita a camada 0, já madura — não é preciso
  redocumentar tabelas/colunas do zero.
- **[Médio impacto]** O mesmo artefato-fonte serve as duas rotas de integração
  em teste (A e B), sem duplicar curadoria enquanto essa decisão amadurece.

### Desvantagens

- **[Alto impacto]** Trabalho de curadoria humana real: nenhuma automação gera
  `entities`/`measures`/`metrics` sozinha, é decisão por tabela.
- **[Médio impacto]** Duas camadas de artefato dbt (`schema.yml` +
  `semantic_models.yml`/`metrics.yml`) a manter em sincronia — divergência é
  possível se uma for atualizada sem a outra.
- **[Médio impacto]** Vocabulário novo para quem já mantém `schema.yml` hoje
  (`entity`/`dimension`/`measure`/`metric`) — precisa de nivelamento leve com
  o time.
- **[Baixo impacto]** Convenção ainda não validada em escala — só o exemplo de
  `cadastro_pj` foi desenhado até este ADR.

### Avaliação

Os ganhos superam os custos: sem esse modelo, adotar dbt Semantic
Layer/MetricFlow vira "mais uma ferramenta" desconectada do que o graphrag-tais
realmente precisa (grain e resolução de conceito). O risco ativo é a curadoria
não acontecer de fato ou divergir do `schema.yml` — mitigado pelo checklist
obrigatório e pela revisão cruzada com quem consome (BI e graphrag-tais).

## Consequências

- **Positivas**: convenção única, testável, compartilhada pelas duas rotas de
  integração em teste; caminho concreto para `concept`/`metric` da IR pararem
  de ser palpite do LLM.
- **Negativas**: esforço de curadoria manual; dois artefatos dbt a manter
  sincronizados; vocabulário novo para o time de dados.
- **Ações decorrentes**:
  - Aplicar o checklist num piloto pequeno antes de expandir para todos os
    schemas — `empreendimento_far_dbt` é candidato natural (já tem `apf` como
    chave de negócio real e bem documentada).
  - Confirmar com o time onde vive hoje o publicador de
    `mcidSemanticRelationships` (pendente desta investigação) antes de
    desenhar em detalhe o adapter `dbt_semantic_manifest_v1` da Rota A.
  - Quando uma rota convergir (ou as duas forem aceitas em paralelo), abrir
    ADR próprio para a extensão formal do contrato do ADR-0011 (`MetricRecord`,
    campos novos em `ColumnRecord`) e do IR do ADR-0006 (resolução de
    `concept`/`metric` contra o manifesto semântico).
  - Atualizar `docs/adr/README.md` com este ADR.

## Referências

- [ADR-0004: OpenMetadata como fonte de metadados](./0004-openmetadata-como-fonte-de-metadados.md)
- [ADR-0006: Interpretação da pergunta em uma representação estruturada (IR)](./0006-interpretacao-da-pergunta-em-representacao-estruturada.md)
- [ADR-0007: Fechamento dos gaps de correção e consistência do v1](./0007-fechamento-dos-gaps-de-correcao-e-consistencia-do-v1.md)
- [ADR-0010: Camada de entrega — tabelas, gráfico, explicação e relatório](./0010-camada-de-entrega-tabelas-grafico-explicacao-relatorio.md)
- [ADR-0011: Contrato detalhado da ingestão OpenMetadata → Neo4j e Qdrant](./0011-contrato-detalhado-da-ingestao-openmetadata-neo4j-qdrant.md)
- [ADR-0012: Desenvolvimento orientado a métricas no retrieval](./0012-desenvolvimento-orientado-a-metricas-no-retrieval.md)
- [ADR-0013: Execução via postgres-mcp e gráfico por chart-spec no v1](./0013-execucao-via-postgres-mcp-e-grafico-chart-spec-no-v1.md)
- [dbt Semantic Layer — Documentação](https://docs.getdbt.com/docs/use-dbt-semantic-layer/dbt-sl)
- [MetricFlow — Semantic models](https://docs.getdbt.com/docs/build/semantic-models)
- [MetricFlow — Metrics](https://docs.getdbt.com/docs/build/metrics-overview)
- Levantamento em `data-application-cidades`: `airflow_lappis/dags/dbt/mcid/`
  (`dbt_project.yml`, `models/**/schema.yml`).
