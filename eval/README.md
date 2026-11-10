# Avaliação do retrieval

Esta pasta é a fonte executável do desenvolvimento orientado a métricas
(*Metric-Driven Development*, MDD) do retrieval. A decisão completa está na
[ADR-0012](../docs/adr/0012-desenvolvimento-orientado-a-metricas-no-retrieval.md).

O escopo termina na produção do subgrafo recuperado:

```text
pergunta
  -> ranking de sementes no Qdrant
  -> expansão e poda no Neo4j
  -> nós, relacionamentos e caminhos recuperados
```

Geração e execução de SQL, resposta do LLM, MetricFlow, glossário, granularidade
e lineage não fazem parte deste benchmark.

## Estado atual

O projeto está em **bootstrap**:

- o motor de métricas e o gate podem ser exercitados offline;
- fixtures sintéticas comprovam as fórmulas sem Qdrant, Neo4j ou Ollama;
- os datasets MCid permanecem em estado `draft` e ainda precisam de revisão humana;
- `mcid-business-v0.1.0.json` contém perguntas analíticas provisórias para
  dashboards, além do caso inicial de descoberta estrutural;
- o collector live emite um `RetrievalTrace` observacional suficiente para o
  scorer atual por fora da pipeline, sem alterar o runtime nem chamar o LLM;
- não existe baseline real aceita.

Portanto, unitários e smoke tests podem passar agora, mas isso **não significa**
que a qualidade do retrieval foi aprovada. O gate real só será habilitado depois
de um gold revisado, um trace completo e uma baseline promovida manualmente.

## Contratos

### Dataset gold

Cada caso contém:

- pergunta, slices e indicação de criticidade;
- relevância graduada dos possíveis nós-semente;
- um ou mais subgrafos gold alternativos;
- nós `Table` e `Column`;
- relacionamentos direcionados `HAS_COLUMN`, `FOREIGN_KEY` ou `SEMANTIC_JOIN`;
- caminhos expressos por IDs estáveis de relacionamento;
- evidência e estado da revisão humana.

O gold representa o **subgrafo mínimo suficiente**, não todo metadado que possa
ter alguma relação com a pergunta. Alternativas permitem registrar mais de um
caminho correto sem penalizar a implementação.

### RetrievalTrace

O retriever deve exportar o trace antes do prompt/LLM, contendo:

- sementes ordenadas, tipo do card, rank e score;
- nós finais tipados;
- relacionamentos finais com ID, tipo, direção e `sql_join_eligible`;
- caminhos candidatos;
- IDs efetivamente serializados no contexto;
- latência por etapa;
- volume textual e estrutural do contexto serializado;
- fingerprint do código, corpus, embeddings, cards e configuração de retrieval.

Não extraia arestas ou caminhos do texto DDL entregue ao LLM. O texto é uma
representação de apresentação e perde informação necessária para a avaliação.

O contrato executável completo está em `eval/retrieval/models.py`. Use como
exemplos:

- `eval/fixtures/retrieval/synthetic-dataset.json`;
- `eval/fixtures/retrieval/perfect-run.json`;
- `eval/fixtures/retrieval/degraded-run.json`;
- `eval/fixtures/retrieval/noisy-run.json`.

Um `RetrievalRun` agrupa todos os traces do dataset e precisa declarar
`dataset_name`, `dataset_version`, `dataset_hash`, `fingerprint` e `metadata`.
A policy inicial exige em `metadata` pelo menos `scope_id` e
`source_snapshot_hash`. O snapshot do grafo e parâmetros variáveis como modelo,
card schema, collection física, `top_k`, fusão, hops e pruning também devem ser
registrados, mas podem diferir da baseline porque são justamente o objeto do
experimento.

## Métricas

O relatório mantém eixos separados, sem produzir um score composto:

- sementes: Recall/Precision@5 e @10, MRR@10 e nDCG@10;
- expansão: ganho de recall e precisão marginal;
- subgrafo: precision, recall e F1 de nós e relacionamentos;
- estrutura: recall de caminhos e suficiência estrutural;
- dependências: recall ponderado;
- ruído: sobre-expansão e nós desconectados;
- operação: taxa de erro e latência p50/p95.

As observações de contexto (`table_node_count`, `column_node_count`,
`schema_context_chars`, `rendered_llm_prompt_chars` e afins) são agregadas em
cobertura, média, p50, p95 e máximo no relatório. O prompt é renderizado pelo
mesmo `ChatPromptBuilder` e template do runtime, mas não é enviado ao LLM.
Essas observações continuam separadas das métricas de qualidade e não formam
um score composto.

`SEMANTIC_JOIN` com `sql_join_eligible=false` pode ser contexto semântico, mas
não satisfaz um caminho de JOIN.

## Comandos

Execute a partir da raiz do repositório:

```bash
make eval-quick \
  EVAL_DATASET=eval/datasets/retrieval/mcid-business-v0.1.0.json
make eval-live
make eval-score EVAL_TRACE=/caminho/para/retrieval-trace.json
make eval-full \
  EVAL_TRACE=/caminho/para/retrieval-trace.json \
  EVAL_BASELINE=eval/baselines/mcid-retrieval-v1.json
```

Ou diretamente nesta pasta:

```bash
make -C eval quick
make -C eval score TRACE=/caminho/para/retrieval-trace.json
make -C eval full \
  TRACE=/caminho/para/retrieval-trace.json \
  BASELINE=baselines/mcid-retrieval-v1.json
```

`quick` valida contratos, executa os unitários do motor e roda um smoke offline.
`eval-live` coleta no container `hayhooks` e pontua os oito casos de negócio
pré-LLM. Para isolar os sinais denso e esparso em runs completos:

```bash
make eval-collect \
  EVAL_CHANNEL=dense \
  EVAL_TRACE=eval/reports/dense-run.json
make eval-collect \
  EVAL_CHANNEL=sparse \
  EVAL_TRACE=eval/reports/sparse-run.json
```

Cada canal usa seu próprio top-5 para a expansão e preserva, separadamente, um
top-10 real para diagnóstico. Recall/Precision@5 medem a seleção que entrou no
grafo; Recall@10, MRR e nDCG medem a chamada diagnóstica, sem fabricar uma
lista ao juntar resultados de chamadas diferentes. Não compare scores brutos
entre canais; compare Recall, MRR, nDCG e os slices calculados pelo runner.

`score` mede um trace sem afirmar aprovação. `full` compara o relatório candidato
com uma baseline aceita e retorna código diferente de zero quando o gate falha.

A policy `retrieval-policy-v0.1.0.json` contém tolerâncias conservadoras de
bootstrap para não regressão. Ela não define um piso absoluto de qualidade e
deve ser revisada junto da primeira baseline real aceita.

### Jornada (Assistente, ADR-0020) com gabarito do banco real

```bash
make eval-journey                       # no container `hayhooks` local; chama o LLM
python -m eval.journey.truth eval/reports/journey-AAAAMMDD   # refaz gabarito.md/.csv de um run
```

Simula o Open WebUI (pergunta → botões → clique em cada trilha) e, nos casos de
número, compara o destaque com o **gabarito**: um SQL escrito à mão no bloco
`truth` do dataset, rodado **ao vivo** no banco real (`eval/journey/truth.py`).
O veredito separa de quem é o defeito: `ok`, `leitura_alternativa` (outra
leitura válida da pergunta), `diferente` (com a `pista` do padrão: dobrado,
fator 100, escala) ou `sem_numero`. As `data_checks` testam o **dado** (linha
de total, último mês vazio, unidade trocada), o catálogo é conferido no Neo4j do
escopo, e tabela que o SQL do sistema usou e o banco não tem aparece como
`tabela_fantasma`. Cada run grava `run.json`, uma transcrição por caso e
`gabarito.md`/`gabarito.csv` (esperado × recebido), para comparar entre runs.

Para rodar contra outro deploy, copie `eval/` para o container dele e chame
`python -m eval.journey.live` com `PYTHONPATH=<cópia>:/app/src` — o collector
usa as pipelines, o catálogo e o banco daquele container.

## Ciclo de uma mudança

1. Registre a hipótese, a métrica primária, os guardrails e os slices afetados.
2. Execute `quick` e preserve o relatório anterior.
3. Implemente a menor mudança que teste a hipótese.
4. Execute novamente `quick` e examine deltas agregados e por caso.
5. Execute `full` antes de concluir a mudança.
6. Corrija a regressão ou leve o tradeoff para revisão.

Use `skills/evaluate-retrieval-change/SKILL.md` para orquestrar esse ciclo.

## Integridade da avaliação

- Nunca atualize gold, policy ou baseline automaticamente.
- Uma mudança no gold exige revisão humana e versionamento.
- Uma baseline só pode ser promovida a partir de um relatório preservado e
  revisado.
- Compare resultados apenas quando dataset e fingerprints obrigatórios forem
  compatíveis.
- Mantenha relatórios candidatos como artefatos locais/CI; versione somente
  baselines aceitas.
- Um dataset `draft`, trace incompleto ou baseline ausente deve produzir estado
  de bootstrap/bloqueio, nunca um falso resultado verde.
