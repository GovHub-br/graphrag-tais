# ADR-0014: Duas personas como dois modos de uso do assistente

- **Status**: Proposto
- **Data**: 2026-08-17
- **Autores**: Davi (graphrag-tais)
- **Revisores**: -

## Introdução ao problema

O assistente foi construído em torno de **uma** pergunta: *"quanto foi X por
UF?"*. Todo o caminho — recuperação top-5, poda de colunas, `EXPANSION_MODE=evidence`,
geração de SQL, execução ([ADR-0013](./0013-execucao-via-postgres-mcp-e-grafico-chart-spec-no-v1.md)),
gráfico e relatório ([ADR-0010](./0010-camada-de-entrega-tabelas-grafico-explicacao-relatorio.md))
— pressupõe que **quem pergunta já sabe o que quer medir e mais ou menos onde
isso mora**.

O levantamento com os usuários mostrou que essa é a **segunda** persona, não a
única:

- **Persona 1 — explorar.** Não conhece as bases. Tem um assunto e quer saber o
  que existe, o que cada base serve para responder e por onde começar. Entrega
  esperada: **bases, SQL e explicação**.
- **Persona 2 — medir.** Já sabe o que quer. Entrega esperada: **bases, SQL,
  explicação e gráfico** — o que o sistema faz hoje.

A decisão precisa ser tomada **agora** porque as duas personas querem coisas
**opostas** da recuperação. A Persona 2 precisa de **precisão** sobre poucas
tabelas — foi para isso que o tuning das Fases 1/2 foi medido e é isso que a
baseline em `eval/baselines/` protege. A Persona 1 precisa de **cobertura**: um
top-5 podado é exatamente a resposta errada para "o que existe sobre habitação".
Servir as duas com uma configuração só significa degradar uma para atender a
outra.

Restrições herdadas: o gold do retrieval está em `draft` e não há baseline aceita
([ADR-0012](./0012-desenvolvimento-orientado-a-metricas-no-retrieval.md)); o seed
recall (~30%) segue sendo o gargalo conhecido; e **55 das 74 tabelas do MCid são
ilhas** no grafo — fato que a Persona 2 contorna e que a Persona 1 precisa
**declarar**.

## Decisão

**Persona vira MODO, e cada modo é uma pipeline própria exposta como um modelo
distinto no OpenWebUI.** O usuário escolhe no dropdown:

| modelo | pipeline | persona | entrega |
|---|---|---|---|
| `Indicador` | `pipelines/query` + `pipelines/delivery` | 2 | bases, SQL, explicação, **gráfico**, relatório `.docx` |
| `Explorar` | `pipelines/explore` | 1 | bases, SQL de amostra, explicação, **lacunas** |

Três regras fecham a decisão:

1. **A Persona 2 não é tocada.** O modo Explorar é **aditivo**: componentes
   novos, pipeline nova, nenhum default de `settings` alterado e nenhuma linha
   do caminho da Persona 2 modificada. Os knobs do modo Explorar (`EXPLORE_*`)
   são lidos **apenas** pela pipeline nova.
2. **Sem roteamento automático nesta fase.** A escolha é explícita. Um
   classificador de intenção erra em silêncio — o usuário não vê o roteamento
   errado, vê uma resposta ruim — e hoje não existe gold de intenção que permita
   medir essa taxa. A IR do
   [ADR-0006](./0006-interpretacao-da-pergunta-em-representacao-estruturada.md)
   não serve como roteador: é produzida **junto com o SQL**, depois da
   recuperação. No lugar do roteador, cada modo **oferece o outro** ao final da
   resposta.
3. **Cobertura é ancorada, nunca gerada.** A lista de tabelas vem do catálogo
   (Neo4j); o LLM prioriza e explica, mas **não inventa**: o que ele cita e não
   existe é descartado e contado. Na Persona 2 uma tabela inventada morre na
   execução do SQL; na Persona 1 ela seria entregue como verdade a quem está
   explorando **justamente porque não conhece o catálogo**.

### Componentes

- **`Neo4jCatalogRetriever`** (novo). Não é uma variante do
  `Neo4jSubgraphRetriever` — é outro componente, para que a Persona 2 permaneça
  literalmente intocada. Três diferenças: (a) devolve os documentos **ordenados
  por relevância** (sementes na ordem do híbrido, depois vizinhos), porque a
  resposta da Persona 1 é um ranking e é isso que o eval pontua; (b) expansão
  **ampla** — mantém todos os vizinhos de 1 hop, elegíveis ou não, porque "o que
  se conecta com isso" faz parte da pergunta; (c) **panorama de domínios** por
  Cypher agregado, que responde "quais bases existem" sem depender de busca
  semântica — pergunta de catálogo não é pergunta vetorial.
- **`ExploreAnswerBuilder`** (novo). Monta a resposta em seis blocos: panorama,
  tabelas candidatas (com **grão**), como se conectam, o que dá para perguntar,
  SQL de amostra, **o que não existe**.
- **Lacunas (`SCOPE_GAPS`)**. Mapa `{conceito: mensagem}` em env, no mesmo padrão
  do `OM_GRAIN_KEYS`. Quando o conceito está fora da ingestão o sistema **já
  acerta ao recusar** — o que faltava era recusar de forma útil.

### Medição

Benchmark próprio em `eval/explore/`, independente de `eval/retrieval` porque a
**unidade do gabarito muda**: um conjunto graduado de tabelas (3 = central, 2 =
útil, 1 = tangencial), não o subgrafo mínimo suficiente. Três famílias:

1. **Ranking**: `recall@k`, `core_recall@k`, `precision@k`, `hit@k`, `MRR@k`,
   `nDCG@k` (ganho graduado) e `MAP@k`, em k ∈ {5, 10, 15}.
2. **Anti-gaming**: recall é trivial de maximizar listando o catálogo inteiro.
   `precision@k` e `listed_ratio` são o contrapeso, e **nenhuma leitura de recall
   vale sem eles ao lado**.
3. **Contrato da persona**: `catalog_fidelity` (único piso absoluto em 1,0),
   `domain_recall/precision`, `gap_disclosure`, `question_groundedness`,
   `sample_sql_grounded`, `answered_rate`.

O gold `mcid-explore-v0.1.0` nasce em **`draft`**: mesma regra do ADR-0012 — os
números são diagnóstico até revisão de domínio, e o CLI marca o gate como não
conclusivo enquanto o status não for `accepted`.

## Alternativas consideradas

### Alternativa A: modelo único com classificador de intenção

- Um só modelo; um LLM (ou heurística) decide se a pergunta é exploratória.
- **Prós**: nada a explicar ao usuário; handoff natural dentro da conversa.
- **Contras**: erro de roteamento é **invisível** e hoje **não mensurável** —
  não há gold de intenção. Custa uma chamada de LLM antes de tudo. E contamina o
  eval: uma queda de métrica passa a ter duas causas possíveis (roteou errado ou
  respondeu mal) sem como separá-las. **Adiada**, não descartada: vira uma fase
  posterior, depois que existir gold de intenção.

### Alternativa B: parametrizar o `Neo4jSubgraphRetriever` para os dois modos

- Um retriever com `profile="sql"|"catalog"` e `expansion_mode` por argumento.
- **Prós**: menos duplicação de Cypher.
- **Contras**: coloca o caminho **medido e estável** da Persona 2 dentro de um
  arquivo que passa a mudar por causa da Persona 1. Toda alteração futura do modo
  Explorar passaria a exigir o gate MDD completo do retrieval da Persona 2.
  **Rejeitada**: a duplicação de duas consultas Cypher é barata; o acoplamento
  entre uma superfície estável e uma em construção, não. Os helpers puros
  (tokenização, grão, nome curto do schema) **são** importados — ali a fonte da
  verdade única vale mais que o isolamento.

### Alternativa C: um "modo exploratório" dentro da resposta da Persona 2

- A mesma resposta traria uma seção "outras tabelas relacionadas".
- **Prós**: zero mudança de interface.
- **Contras**: não resolve o problema real — o `top_k` e a poda continuam
  calibrados para precisão, então a seção extra listaria justamente o que o
  tuning removeu. E infla a resposta da Persona 2, que hoje é enxuta de
  propósito. **Rejeitada.**

### Não fazer nada / manter status quo

A Persona 1 continua recebendo "refine a pergunta" quando pergunta o que existe
— ou, pior, um SQL sobre a primeira tabela plausível, que ela não tem como
conferir. **Não aceitável**: é a persona que mais precisa do catálogo e a única
sem nenhuma forma de navegá-lo.

## Tradeoffs

### Vantagens

- **[Alto impacto]** O baseline medido da Persona 2 fica protegido por
  construção, não por disciplina: os arquivos dela não mudam.
- **[Alto impacto]** Cada modo é medido contra o seu próprio gold, sem
  contaminação entre objetivos opostos (precisão vs. cobertura).
- **[Médio impacto]** A Persona 1 sai mais barata e mais rápida que a Persona 2
  (uma chamada de LLM, sem execução, gráfico ou `.docx`), apesar do contexto maior.
- **[Médio impacto]** "Quais bases existem" passa a ter resposta determinística,
  imune a alucinação — vem de Cypher agregado, não de embedding.

### Desvantagens

- **[Médio impacto]** O usuário precisa escolher o modo. Se escolher errado,
  recebe uma resposta com a forma errada (mitigado: cada modo oferece o outro).
- **[Médio impacto]** Duas Cypher parecidas em dois arquivos. Divergência
  silenciosa é risco real e fica anotado como tal.
- **[Baixo impacto]** Mais uma superfície para manter (prompt, builder, gold,
  política de gate).

### Avaliação

Os ganhos superam os custos. O custo central — escolha manual do modo — é
**visível e reversível**; o custo que a alternativa automática traria — erro de
roteamento silencioso e não mensurável — não é nenhum dos dois. O risco ativo
que permanece é a **divergência entre as duas Cypher**; a mitigação é o teste de
ordenação com store canned, que fixa o contrato de ordem de que o eval depende.

## Consequências

- **Positivas**: a Persona 1 passa a ser atendida; a recusa vira informação
  (`SCOPE_GAPS`); o projeto ganha um segundo benchmark, com métricas de ranking
  que o benchmark atual não tinha (`nDCG` graduado, `MAP`).
- **Negativas**: duplicação de Cypher; um gold novo em `draft` que precisa de
  revisão de domínio antes de valer como gate; `SCOPE_GAPS` é conhecimento de
  escopo mantido à mão, que envelhece se a ingestão mudar.
- **Ações decorrentes**:
  1. Revisão de domínio do `mcid-explore-v0.1.0` (18 casos) para sair de `draft`.
  2. Preencher `SCOPE_GAPS` no `.env` — sem ele, `gap_disclosure` mede só o que
     o LLM declarar por conta própria.
  3. Rodar `make eval-explore` com serviços e VPN para produzir a primeira
     baseline diagnóstica.
  4. Decisão de escopo pendente (`__dados_brutos.*`, `empreendimento_rural.*`):
     se ficarem fora, `SCOPE_GAPS` deixa de ser conveniência e vira a forma
     honesta de responder.

## Referências

- [Plano de implementação das duas personas](../20260817-plano-duas-personas.md)
- [ADR-0010](./0010-camada-de-entrega-tabelas-grafico-explicacao-relatorio.md) — camada de entrega da Persona 2
- [ADR-0012](./0012-desenvolvimento-orientado-a-metricas-no-retrieval.md) — MDD: gold em `draft` não é gate
- [ADR-0013](./0013-execucao-via-postgres-mcp-e-grafico-chart-spec-no-v1.md) — execução e gráfico
- [Plano de 13/08](../20260813-plano-proximos-passos.md) — item C2 (recusa informativa) e o prior do Superset
