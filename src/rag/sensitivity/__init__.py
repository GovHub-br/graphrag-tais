"""Mascaramento de dado sensível na saída (ADR-0018).

Reabre a "Alternativa C" que o [ADR-0016](../../../docs/adr/0016-guardrails-de-resposta.md)
recusou: lá, a proteção do VALOR era do acesso mascarado no banco, e a
aplicação só protegia a CONVERSA. Onde esse acesso não cobre a base, esta
camada cobre — pseudonimizando o valor no único ponto por onde toda linha real
passa, o `McpSqlExecutor`.

Três peças, todas função pura testável no host (mesmo desenho de
`rag.guardrails`):

- `catalog` — lê o mapeamento `(schema.tabela → colunas sensíveis)` do arquivo
  versionado e o indexa;
- `resolver` — descobre, com `sqlglot`, de qual coluna de qual tabela veio cada
  coluna do resultado (segue alias, ignora agregação, fecha na dúvida);
- `mask` — decide a regra por coluna e aplica HMAC-SHA256 com segredo.

Uso (é o que o executor faz):

    from rag.sensitivity import mask_rows

    rows, masked_columns = mask_rows(rows, columns, sql_executado)

Desligado por omissão de catálogo: com o arquivo ausente ou vazio, `mask_rows`
devolve as linhas intactas e o sistema se comporta exatamente como antes.
"""
from __future__ import annotations

from rag.sensitivity.catalog import Catalog, load, normalize, normalize_table, parse
from rag.sensitivity.mask import Plan, Rule, apply, mask_rows, plan
from rag.sensitivity.resolver import Projection, Resolution, resolve, source_tables

__all__ = [
    "Catalog",
    "Plan",
    "Projection",
    "Resolution",
    "Rule",
    "apply",
    "load",
    "mask_rows",
    "normalize",
    "normalize_table",
    "parse",
    "plan",
    "resolve",
    "source_tables",
]
