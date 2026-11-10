"""Catálogo do escopo (Neo4j) × banco que executa o SQL (information_schema).

A premissa que o CLAUDE.md manda vigiar: "o catálogo descreve o MESMO banco
que executa o SQL". Quando ela falha, o motor escreve SQL correto para uma
tabela que não existe, o banco recusa e a resposta sai sem número — parecendo
erro do modelo. Em 09/11/2026 o escopo `mcid` de produção tinha 17 tabelas
(famílias `*historico*` e `*reloginho*`) que o banco não tem, e eram elas que
derrubavam "total de UH do FAR" e "Pró-Moradia por UF".

Rode depois de cada ingestão, no container do deploy:

    docker exec -i -e PYTHONPATH=/app/src hayhooks python - < scripts/audit_catalog_vs_db.py

Só lê `information_schema` e o Neo4j — nenhuma linha de dado. Sai JSON;
código de saída 1 se houver tabela do catálogo ausente no banco.
"""
from __future__ import annotations

import json
import sys

from neo4j import GraphDatabase

from rag.components.executors.mcp_sql_executor import McpSqlExecutor
from rag.settings.configs import settings as s


def main() -> int:
    driver = GraphDatabase.driver(s.NEO4J_URL, auth=(s.NEO4J_USERNAME, s.NEO4J_PASSWORD))
    try:
        with driver.session(database=s.NEO4J_DATABASE) as ss:
            rows = ss.run("MATCH (t)-[:HAS_COLUMN]->(c) RETURN t.schema AS sc, t.name AS t, "
                          "collect(c.name) AS cols").data()
    finally:
        driver.close()
    # `t.schema` guarda o FQN (`Servico.db.schema`); o banco só conhece `schema`.
    catalogo = {(r["sc"].split(".")[-1], r["t"]): set(r["cols"]) for r in rows}
    schemas = sorted({sc for sc, _ in catalogo})

    ex = McpSqlExecutor(direct_only=True)
    ex.max_rows = 1_000_000  # inventário: o teto de 1.000 linhas da resposta não vale aqui
    lista = ", ".join(f"'{x}'" for x in schemas)
    r = ex.run(sql="SELECT table_schema, table_name, column_name FROM information_schema.columns "
                   f"WHERE table_schema IN ({lista})")
    if r["error"]:
        print(json.dumps({"erro": r["error"]}, ensure_ascii=False))
        return 2
    banco: dict = {}
    for x in r["rows"]:
        banco.setdefault((x["table_schema"], x["table_name"]), set()).add(x["column_name"])

    sem_tabela = sorted(f"{a}.{b}" for (a, b) in catalogo if (a, b) not in banco)
    sem_coluna = {f"{a}.{b}": sorted(cols - banco[(a, b)])
                  for (a, b), cols in catalogo.items() if (a, b) in banco and cols - banco[(a, b)]}
    print(json.dumps({
        "escopo": s.OM_SCOPE_ID, "schemas": schemas,
        "tabelas_no_catalogo": len(catalogo), "colunas_lidas_do_banco": len(r["rows"]),
        "tabelas_do_catalogo_sem_tabela_no_banco": sem_tabela,
        "colunas_do_catalogo_sem_coluna_no_banco": sem_coluna,
        "tabelas_do_banco_fora_do_catalogo": len([k for k in banco if k not in catalogo]),
    }, ensure_ascii=False, indent=1))
    return 1 if sem_tabela else 0


if __name__ == "__main__":
    sys.exit(main())
