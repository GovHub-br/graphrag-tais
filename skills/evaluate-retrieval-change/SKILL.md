---
name: evaluate-retrieval-change
description: Avalie mudanças no retrieval do GraphRAG com o processo de Metric-Driven Development (MDD) do projeto. Use ao alterar embeddings, dimensão ou instruções de embedding, conteúdo ou schema dos cards, recuperação híbrida no Qdrant, fusão, top_k, reranking, seleção de seeds, expansão ou poda no Neo4j, relacionamentos, RetrievalTrace, datasets gold, baselines ou políticas de qualidade.
---

# Avaliar mudança de retrieval

## Preparar a avaliação

1. Leia `docs/adr/0012-desenvolvimento-orientado-a-metricas-no-retrieval.md`.
2. Leia `eval/README.md` por inteiro e use somente os comandos e contratos ali publicados.
3. Inspecione o diff e classifique a mudança antes de executar a avaliação:
   - indexação semântica: modelo, dimensão, instrução, card ou conteúdo;
   - recuperação de seeds: denso, esparso, híbrido, filtro, fusão, `top_k` ou reranking;
   - grafo: relacionamento, expansão, travessia ou poda;
   - observabilidade: `RetrievalTrace`, fingerprint ou versão;
   - governança da avaliação: gold, baseline ou policy;
   - alheia ao retrieval.
4. Para uma mudança alheia ao retrieval, registre `retrieval_evaluation: not_applicable`, justifique a classificação e execute apenas os testes normais afetados. Não reivindique melhoria nem aprovação da qualidade do retrieval.
5. Para uma mudança que afete retrieval, registre antes da implementação ou medição:
   - hipótese causal;
   - métrica primária e direção esperada;
   - guardrails que não podem regredir;
   - slices e casos provavelmente afetados;
   - dataset, versão, baseline aceita e fingerprints esperados.
6. Registre esses dados pelo mecanismo definido em `eval/`. Não crie cálculo, runner ou regra paralela dentro da skill.

## Executar o ciclo MDD

1. Valide o dataset pelo alvo correspondente de `eval/Makefile`.
2. Execute o perfil `quick` no estado anterior e preserve seu relatório como baseline da iteração.
3. Implemente a menor mudança capaz de testar a hipótese.
4. Execute o mesmo perfil `quick` no candidato, com dataset, ambiente e política equivalentes.
5. Compare os relatórios pelo gate oficial de `eval/`; nunca calcule métricas ou determine o veredito manualmente.
6. Analise, nesta ordem:
   - veredito e invariantes;
   - métricas agregadas e deltas;
   - slices;
   - casos individuais que ganharam ou regrediram;
   - custo, latência e tamanho do contexto, quando medidos.
7. Ajuste ou reverta a hipótese quando o quick indicar regressão. Repita sem editar o gold ou relaxar a policy para obter aprovação.
8. Execute os testes unitários publicados por `eval/Makefile`.
9. Execute o perfil `full` antes de concluir qualquer mudança que afete retrieval.
10. Execute novamente o gate oficial entre a baseline aceita e o relatório full candidato.

Mantenha toda lógica executável, métricas, thresholds e políticas em `eval/` e nos alvos de `eval/Makefile`. Use a skill apenas para orquestrar o ciclo, interpretar os artefatos e relatar o resultado.

## Proteger a integridade da avaliação

- Nunca atualize gold, baseline aceita ou policy automaticamente.
- Separe alterações de gold, baseline e policy da feature avaliada e submeta-as a revisão explícita.
- Nunca troque a baseline porque o candidato perdeu.
- Nunca compare datasets, versões, fingerprints ou espaços de embedding incompatíveis.
- Reindexe o corpus candidato pelo fluxo documentado quando a mudança invalidar os vetores ou cards existentes.
- Exija um `RetrievalTrace` pré-LLM suficiente para reproduzir seeds, expansão, poda e ranking envolvidos na mudança.
- Trate o runner e o gate de `eval/` como fontes exclusivas do resultado. Não use impressão subjetiva, RAGAS, resposta do LLM ou SQL gerado como gate substituto.
- Limite o gate atual a `Table`, `Column` e `Relationship` como aresta, conforme as ADRs 0011 e 0012.

## Declarar cobertura incompleta

Declare explicitamente a cobertura ausente e nunca diga que a qualidade passou quando ocorrer qualquer condição abaixo:

- dataset em estado `draft`;
- ausência de baseline aceita;
- `RetrievalTrace` ausente ou incompleto;
- infraestrutura necessária indisponível;
- dataset, versão ou fingerprint incompatível;
- execução full não realizada;
- erro ou invariante inválida no relatório.

Diferencie “testes unitários passaram” de “gate de qualidade passou”. Trate resultados sem baseline aceita como bootstrap diagnóstico, não como comparação aprovada.

## Relatar a conclusão

Informe de forma concisa:

- classificação da mudança;
- hipótese, métrica primária e guardrails;
- IDs, versões e fingerprints de dataset, baseline e candidato;
- status de quick, unitários, full e gate;
- deltas agregados, regressões por slice e casos relevantes;
- veredito emitido pela policy;
- cobertura faltante e limitações;
- decisão: iterar, reverter ou encaminhar para revisão.

Só declare a mudança pronta quando unitários e full terminarem, o gate oficial aprovar e nenhuma condição de cobertura incompleta permanecer.
