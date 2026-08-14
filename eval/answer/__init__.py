"""Avaliação da qualidade da RESPOSTA (item 0.3 do plano).

Complementa `eval.retrieval`, que por decisão do ADR-0012 termina no subgrafo.
As métricas de fidelidade, JOIN autorizado e grão são determinísticas (parse do
SQL contra o contexto recuperado); execution accuracy depende de `reference_sql`
no gold e é trabalho de domínio.
"""
