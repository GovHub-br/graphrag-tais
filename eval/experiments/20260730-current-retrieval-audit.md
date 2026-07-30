# Auditoria diagnóstica do retrieval atual — 2026-07-30

## Status

Este é um diagnóstico do sistema atual, não uma baseline aceita nem um gate
verde. O dataset `mcid-business-retrieval 0.1.0` continua `draft`, com oito
perguntas analíticas de dashboard e revisão de domínio pendente.

- Dataset hash:
  `7524635f554cdf4912d72611cee7e5c55235f94b946c15b96a592b01337f886c`
- Run híbrido de referência:
  `live-retrieval-20260730T181132775301Z`
- Fingerprint híbrido:
  `f4f849fbcfd5097fe53c49f1551d2df1a6c9839551ea96cdacedb47b48b24a4b`
- Snapshot: 1.289 cards no Qdrant, 1.289 nós e 1.239 relações no Neo4j.
- Execução: 8/8 casos sem erro; cobertura do tamanho do prompt de 100%.

O `quick` passou com 25 testes e smoke. O perfil `full` pontuou o candidato,
mas o gate parou corretamente porque o dataset é `draft` e não existe baseline
aceita.

## O que foi medido

O collector executa as mesmas peças de embedding, Qdrant e Neo4j da pipeline,
antes do LLM:

- top-5 real que entrou na expansão;
- top-10 real e separado para Recall@10, MRR e nDCG;
- ranking denso, esparso e híbrido em runs independentes;
- nós, relações, caminhos e elegibilidade de JOIN;
- prompt renderizado pelo mesmo `ChatPromptBuilder` e templates da pipeline;
- snapshots, versões, scope, fingerprint e latência.

O collector também falha fechado antes da expansão se uma semente não tiver
`node_id`, `point_id`, scope, tipo ou score válidos. O snapshot real valida,
ponto a ponto, hash de conteúdo, schema do card, perfil de embedding, run de
ingestão e versão da fonte.

Não há execução do LLM, SQL ou gráfico dentro dessas métricas. A validação
E2E do endpoint foi registrada separadamente.

## Embedding e recuperação vetorial

Perfil atual:

- denso: `aroxima/gte-qwen2-1.5b-instruct:f16`, 1.536 dimensões;
- esparso: `Qdrant/bm25`;
- fusão: RRF do Qdrant;
- top-5 para produção e top-10 diagnóstico;
- cards v1, sem truncamento observado.

| Canal | Recall@5 | Recall@10 | MRR@10 | nDCG@10 | Suficiência | Prompt médio |
|---|---:|---:|---:|---:|---:|---:|
| Denso | 22,76% | 32,48% | 0,625 | 0,368 | 7/8 | 16.144 chars |
| Esparso | 21,51% | 30,40% | 0,604 | 0,342 | 6/8 | 24.946 chars |
| Híbrido | 30,52% | 39,27% | 0,531 | 0,406 | 8/8 | 21.453 chars |

O híbrido aumenta Recall@5 em 34% sobre o denso e 42% sobre o esparso. Ele
também é o único a recuperar estrutura suficiente nos oito casos e mantém
nDCG superior nos três runs. O MRR híbrido, porém, oscila bastante por causa
de empates no RRF.

Conclusão sobre o modelo: o denso traz sinal complementar e não deve ser
substituído agora. A implementação envia a pergunta crua ao embedder. A
família GTE-Qwen2 documenta instrução no lado da query, então o próximo teste
de modelo deve comparar o perfil atual com um prefixo de instrução, usando
fingerprint próprio, antes de considerar troca de modelo:
<https://huggingface.co/Alibaba-NLP/gte-Qwen2-1.5B-instruct>.

## Estabilidade do RRF

Três runs adicionais com o mesmo dataset, código, configuração e snapshots
produziram:

- Recall@5 fixo em 30,52% e Recall@10 fixo em 39,27%;
- MRR@10 entre 0,604 e 0,667;
- nDCG@10 entre 0,422 e 0,440;
- ordem do top-5 diferente em 6/8 casos;
- membros do top-5 diferentes em 1/8 casos;
- scores empatados recorrentes, especialmente `0.5`, `0.33333334` e `0.25`.

O empate na fronteira do top-5 muda o subgrafo. Em
`far-ranking-valor-001`, o prompt variou de 34.842 caracteres e 310 colunas
para 37.715 caracteres e 349 colunas, sempre com 9 tabelas, sem mudança de
configuração.

## Expansão no Neo4j

Inventário:

- 74 tabelas;
- 1.215 colunas;
- 1.215 relações `HAS_COLUMN`;
- 24 relações `SEMANTIC_JOIN`;
- somente 6 relações são `sql_join_eligible=true`.

No híbrido:

- node/relationship/path recall: 100%;
- suficiência estrutural: 100%;
- recall de JOIN elegível e caminho: 100%;
- JOIN não autorizado: 0%;
- node precision: 12,5% no run de referência;
- relationship precision: 11,2%;
- ganho de recall da expansão: 69,5 pontos percentuais;
- precisão marginal da expansão: 8,9%;
- sobre-expansão: 27,1× a 27,8× nos três runs.

A expansão é segura quanto à elegibilidade de JOIN e resolve os dois casos
multi-tabela, mas recupera estrutura demais. Ela expande todos os vizinhos de
um hop e serializa todas as colunas das tabelas alcançadas.

## Tamanho e qualidade do contexto

O tamanho abaixo é do prompt realmente renderizado, incluindo system/user
messages, pergunta e JOINs — não uma estimativa por JSON.

No run híbrido de referência:

- média: 21.453 caracteres;
- p50: 26.700;
- p95: 35.310;
- máximo: 35.562;
- média de 5,8 tabelas e 186,4 colunas.

| Slice | Prompt médio | Tabelas | Colunas | Node precision | Sobre-expansão |
|---|---:|---:|---:|---:|---:|
| FAR | 27.003 | 7,5 | 243,5 | 2,46% | 40,1× |
| FDS | 34.695 | 8,0 | 313,3 | 2,40% | 43,1× |
| Conjuntura | 4.510 | 2,3 | 21,3 | 29,91% | 2,58× |

Portanto, o contexto está grande demais em relação ao subgrafo útil,
principalmente em FAR/FDS. O problema não é apenas caber na janela do modelo;
é a baixa densidade de evidência relevante e a contaminação entre domínios.

## Scope

Todos os 1.289 cards do snapshot atual têm `scope_id=mcid`, e não houve
vazamento observado. Entretanto, os retrievers são chamados com
`scope_filter=null`. Isso contraria o isolamento obrigatório do ADR-0011 e
vira risco real assim que uma collection tiver mais de um scope.

## Endpoint e correção do Hayhooks

O `mcp-haystack 1.4.0` foi mantido e o SDK foi fixado em `mcp 1.29.0`, pois a
integração ainda usa a API v1. O container `hayhooks` está saudável, os imports
funcionam e `pip check` não encontrou dependências quebradas.
Versões oficiais: <https://pypi.org/project/mcp-haystack/1.4.0/> e
<https://pypi.org/project/mcp/1.29.0/>.

A pergunta “Quais são os 10 empreendimentos FAR com maior valor contratado?”
retornou HTTP 200, executou SQL pelo MCP e gerou PNG. O resultado não está
correto para negócio:

- `empreendimento` e `ficha_empreendimento` têm 1.646 linhas para 823 APFs;
- cada APF aparece exatamente duas vezes, com linhas idênticas;
- o top-10 contém somente cinco APFs distintos;
- o renderer agrupa nomes repetidos e soma os pares, dobrando os valores no
  gráfico.

Esse erro é de grain/execução após o retrieval e precisa de avaliação E2E
complementar. Trocar apenas a tabela recuperada não resolve.

## Próximos experimentos, em ordem

1. **Filtro obrigatório de scope.** Aplicar `scope_id` em toda consulta
   Qdrant e adicionar caso adversarial multi-scope. No snapshot atual, a
   expectativa é preservar todas as métricas.

2. **Fusão determinística antes do corte.** Recuperar um pool maior e aplicar
   desempate estável antes de selecionar cinco seeds. Meta: mesmos membros e
   ordem em 8/8 casos por pelo menos três runs, sem regressão de Recall@5 ou
   suficiência estrutural.

3. **Roteamento por domínio e intenção.** Evitar mistura FAR/FDS/conjuntura e
   distinguir pergunta mono-tabela de multi-tabela.

4. **Expansão com evidência.** Em consulta mono-tabela, subir seeds para a
   tabela principal sem unir todos os vizinhos. Percorrer JOIN exato somente
   quando a intenção for multi-tabela ou os dois lados tiverem evidência.

5. **Poda de colunas e orçamento.** Serializar colunas selecionadas, métricas,
   identificadores e chaves de JOIN. Meta experimental: no máximo 1–2 tabelas
   e 15 colunas em mono-tabela; 2–3 tabelas e 30 colunas em multi-tabela;
   prompt p95 abaixo de 12 mil caracteres, mantendo suficiência e JOIN recall
   em 100% e JOIN não autorizado em zero.

6. **Experimento de instrução do GTE.** Comparar pergunta crua com um prefixo
   de negócio em português. Só depois avaliar troca de modelo ou reindexação.

7. **Guardrail E2E de grain.** “Top 10 empreendimentos” deve produzir dez APFs
   distintos; `apf` deve ter teste de unicidade/not-null; o renderer não deve
   somar duplicatas silenciosamente.

Não aumentar `top_k` agora: o recall final já chega a 100% por espalhamento no
grafo, e mais seeds tendem a ampliar o ruído.
