# Prompts

Todo texto que vai para o LLM mora aqui — um arquivo `.md` por prompt. O código
carrega com `prompts.load("<nome>")` (cru) ou `prompts.render("<nome>", **vars)`
(`str.format`). Ver `__init__.py`.

| arquivo | quem usa | papel |
| --- | --- | --- |
| `query_system.md` | `pipelines/query` (P2) | gera o SQL: escolha de tabela por grão, JOINs autorizados, saída JSON |
| `query_user.md` | `pipelines/query` (P2) | **template Jinja**: subgrafo recuperado + JOINs + pergunta |
| `result_summary_system.md` | `components/answer/result_explainer.py` (P2) | 1 parágrafo explicando o resultado que o SQL trouxe |
| `result_summary_user.md` | `components/answer/result_explainer.py` (P2) | `str.format`: pergunta + resumo estatístico do resultado |
| `chart_spec_system.md` | `components/charts/chart_spec.py` (P2) | escolhe bar/line/none (só quando a heurística não decide) |
| `chart_spec_user.md` | `components/charts/chart_spec.py` (P2) | `str.format`: pergunta + colunas + amostra |
| `sql_executor_system.md` | `components/executors/mcp_sql_executor.py` | agente que executa e autocorrige o SQL via postgres-mcp |
| `sql_executor_user.md` | `components/executors/mcp_sql_executor.py` | `str.format`: pergunta + schema + SQL a executar |
| `explore_system.md` | `pipelines/explore` (P1) | guia do catálogo: panorama, tabelas, perguntas, lacunas |
| `explore_user.md` | `pipelines/explore` (P1) | **template Jinja**: domínios + tabelas recuperadas + JOINs + pergunta |

**Guardrails (ADR-0016).** `query_system`, `explore_system` e
`result_summary_system` carregam um bloco de **RECUSAS**: não gerar SQL que
identifique pessoa, recusar o que está fora do objetivo do sistema e nunca
escrever que o modelo *acessou/consultou/leu* o dado (ele escreve o SQL; quem
executa é o banco). As duas primeiras devolvem `refusal` no JSON — quando vem
preenchido, o answer builder renderiza só a explicação. ⚠️ O **texto das
recusas** não mora aqui: está em `rag/guardrails/messages.py`, porque é resposta
pronta ao usuário, não texto que vai para o modelo. Este diretório é só o que o
LLM lê.

**Jinja vs `str.format`.** Os `*_user.md` das pipelines (`query_user`,
`explore_user`) são renderizados pelo `ChatPromptBuilder` do Haystack, então usam
Jinja (`{{ var }}`, `{% for %}`). Os prompts montados em Python usam
`{placeholder}` do `str.format` — por isso não podem ter chave literal.

⚠️ Mudar um prompt de `query_*` é mudar o comportamento **medido** da Persona 2
(ver `eval/` e o gate do ADR-0012), não é formatação.
