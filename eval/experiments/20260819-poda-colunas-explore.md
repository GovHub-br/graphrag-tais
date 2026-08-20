# Poda de colunas do modo Explorar — relevância por radical + IDF local

- **Data**: 2026-08-19
- **Escopo**: Persona 1 (`pipelines/explore`). A Persona 2 **não foi tocada**.
- **Motivação**: numa pergunta sobre *localização*, o card de
  `empreendimento_far.empreendimento` (27 colunas) chegou ao LLM sem `municipio`
  nem `uf`. O modelo obedeceu o card — a amostra de dados saiu sem localização.
- **Restrição do experimento**: a poda existe para segurar prompt e latência
  (Fase 1/2). Só valem mudanças que **não piorem** esses dois.

## Diagnóstico

A poda mantém PK, chave de JOIN, coluna-semente e "coluna que casa com a
pergunta"; o teto limita só o preenchimento por ordinal. Logo **o teto não
cortava o que importa — o casamento é que não reconhecia**. O casamento era
substring crua (`_col_matches`, retriever da Persona 2):

| coluna | descrição | entrava? | por quê |
|---|---|:--:|---|
| `situacao_empreendimento` | "…a partir dos **dados** da CAIXA" | ✅ | casou com `dados`, enchimento da pergunta |
| `percentual_execucao_fisica` | "…e **dados** prioritarios da CAIXA" | ✅ | idem |
| `municipio` | "Municipio onde o empreendimento esta **localizado**" | ❌ | pergunta diz "localiza**ção**" |

## Mudança

1. **Matcher próprio da Persona 1** (`neo4j_catalog_retriever.py`): remove
   acento, compara **radicais** (sufixo flexional removido) e descarta palavras
   genéricas de catálogo (`dados`, `tabela`, `base`, `contem`, …).
2. **Relevância por IDF local**: o peso de um termo é `1/nº de colunas da tabela
   que ele atinge. "empreendimento" está em quase toda descrição e não distingue
   nada; "localiz" está em duas — e são as duas que a pergunta queria. Match no
   **nome** pesa 3× o match na descrição.
3. **Orçamento estrito**: o teto passa a valer para o card inteiro (antes as
   "essenciais" entravam sem teto). Prioridade: estruturais → relevantes →
   sementes → ordinal. É o que garante que subir a relevância não engorde o prompt.
4. **Orçamento diferenciado** semente 10 / expansão 6 (como a Persona 2 já fazia
   com 25/6): redistribui o mesmo custo para a tabela que é a resposta.

## Método

`scripts/measure_explore_context.py` — 18 perguntas do gold `mcid-explore-v0.1.0`
+ a pergunta que motivou o caso. Sem LLM: compara recuperação com recuperação.
Mede tokens do card, latência da recuperação e **recall de coluna** = das colunas
que a tabela REALMENTE tem sobre o conceito da pergunta (verdade lida do Neo4j),
quantas sobreviveram à poda.

## Resultado

| config | tokens média | tokens p95 | recall de coluna | latência média |
|---|--:|--:|--:|--:|
| **baseline** (8/8, matcher antigo) | 2220 | 3521 | 0,489 | 0,15s |
| 8/8, matcher novo | 2166 (−2,4%) | 3526 (+0,1%) | 0,539 | 0,12s |
| **10/6 (adotado)** | **2214 (−0,3%)** | **3489 (−0,9%)** | **0,575 (+18%)** | **0,15s** |
| 12/4 | 2256 (+1,6%) | 3446 (−2,1%) | 0,685 (+40%) | 0,12s |
| 14/5 | 2357 (+6,2%) | 3578 (+1,6%) | 0,723 (+48%) | 0,12s |

**Adotado 10/6**: é a configuração que sobe o recall sem piorar nenhum dos dois
eixos protegidos (média −0,3%, p95 −0,9%). **12/4 fica registrado como a opção
recomendada se +1,6% de prompt médio for aceitável** — compra +40% de recall e
ainda melhora o p95.

Por caso (10/6 contra baseline): `localizacao-censitario` **0,211 → 0,658**;
`far-mapa` 0,625 → 0,750; `far-empreendimento-individual` 0,839 → 0,581 (o teto
estrito custa aqui); `conjuntura-renda` 1,0 mantido; `conjuntura-emprego` 0,0 nos
dois (a sonda mede colunas de trabalho social em tabelas do FAR — provavelmente
sonda ruim, não regressão).

## Rejeitado por custo

**Listar os nomes das colunas podadas no card** (`EXPLORE_MAX_OMITTED_NAMES`):
custa +10% (8 nomes) a +16% (15 nomes) de prompt e **o ganho não é visível nas
métricas de hoje** — o recall de coluna conta coluna detalhada, não nome citado.
Fica implementado e **desligado por default**: o benefício real (a resposta parar
de negar coluna que existe) precisa de uma métrica própria antes de valer o custo.

## Dados

`20260819-poda-colunas-explore-antes.json` (baseline) e `-final.json` (adotado).
Reproduzir:

```bash
docker exec -w /app -e PYTHONPATH=/app:/app/src hayhooks \
  python scripts/measure_explore_context.py --output /app/run.json
```

## Pendente

O mesmo defeito de casamento existe na **Persona 2** (`_col_matches` mora lá e
ela usa a mesma regra, com tetos 25/6). Corrigir lá exige o gate do ADR-0012
(medir retrieval antes/depois) e **não foi feito aqui**.
