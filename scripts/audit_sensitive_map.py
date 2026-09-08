"""Auditoria de cobertura do catálogo de sensibilidade (ADR-0018).

O catálogo vem do inventário do dbt; o banco tem mais coisa do que o dbt modela.
Este script confronta um com o outro e responde duas perguntas:

  A) que coluna com CARA de dado pessoal o catálogo NÃO cobre?  -> risco
  B) que regra do catálogo não tem coluna correspondente?       -> regra morta

Rode depois de cada reimportação do inventário, e quando um schema novo entrar
no Trino:

    docker cp scripts hayhooks:/app/scripts
    docker exec -w /app -e PYTHONPATH=/app/src hayhooks \
        python scripts/audit_sensitive_map.py

Só lê `information_schema` — nenhuma linha de dado é acessada.

⚠️ **O trino-mcp corta o resultado em 5.000 linhas sem avisar.** Por isso a
leitura é paginada. A primeira versão desta auditoria não era: leu 5.000 de
19.132 colunas e concluiu que metade das regras do catálogo estava morta —
artefato do truncamento, não achado. Qualquer consulta de inventário aqui
precisa paginar.

O casamento é por NOME de coluna (regex grosso): falso positivo é barato, o que
não pode é escapar. `RUIDO` tira o que denuncia identificador de sistema
(`idtelefone`, `dtemail`) em vez de dado pessoal.
"""
import re, sys
from rag.settings.configs import settings
settings.MCP_DIRECT_TIMEOUT = 240
from rag.components.executors.mcp_sql_executor import McpSqlExecutor
from rag.sensitivity import catalog as catalog_mod

SCHEMAS = ("bronze", "salic", "salic_bronze", "agentes", "cotas", "bbagil",
           "bbagil_bronze", "transferegov", "transferegov_bronze", "minc")
PAGINA = 4000

SUSPEITO = re.compile(
    r"cpf|cnpj|cgc|senha|password|e-?mail|telefone|celular|"
    r"conta_?banc|nrconta|numconta|agencia|nome_?civil|nome_?social|"
    r"nascimento|\brg\b|passaporte|titulo_?eleitor|\bpis\b|\bnis\b", re.I)
RUIDO = re.compile(r"nomeprojeto|nome_?projeto|nome_?orgao|nome_?municipio|"
                   r"nome_?uf|nome_?area|nome_?segmento|nome_?arquivo|"
                   r"nome_?fantasia|idtipo|idato|idhistorico|idtexto|"
                   r"^dt|^id[a-z]*email$", re.I)

cat = catalog_mod.load(settings.SENSITIVE_MAP_PATH)
e = McpSqlExecutor(direct_only=True, max_rows=100000); e.direct_timeout = 240
lista = ", ".join(f"'{s}'" for s in SCHEMAS)

todas, offset = [], 0
while True:
    r = e.run(sql=f"""SELECT table_schema, table_name, column_name
                      FROM information_schema.columns
                      WHERE table_schema IN ({lista})
                      ORDER BY table_schema, table_name, column_name
                      OFFSET {offset} LIMIT {PAGINA}""")
    if not r["exec_ok"]:
        print("FALHOU:", r.get("error", "")[:200]); sys.exit(1)
    todas.extend(r["rows"])
    if len(r["rows"]) < PAGINA:
        break
    offset += PAGINA
print(f"colunas lidas: {len(todas)}  (esperado 19132)\n")

cobertas, descobertas = 0, {}
for x in todas:
    col, tab = x["column_name"], x["table_name"]
    if not SUSPEITO.search(col) or RUIDO.search(col):
        continue
    if cat.strategy(f"{x['table_schema']}.{tab}", col):
        cobertas += 1
    else:
        descobertas.setdefault(col.lower(), set()).add(f"{x['table_schema']}.{tab}")

print(f"A) coluna com CARA de dado pessoal")
print(f"   coberta pelo catálogo : {cobertas}")
print(f"   NÃO coberta           : {sum(len(v) for v in descobertas.values())} "
      f"ocorrências / {len(descobertas)} nomes\n")
for nome, tabs in sorted(descobertas.items(), key=lambda kv: -len(kv[1]))[:30]:
    print(f"     {nome:36} {len(tabs):4}x   ex: {sorted(tabs)[0]}")

existentes = {(x["table_name"].lower(), x["column_name"].lower()) for x in todas}
mortas = [(t, c) for t, cols in cat.by_table.items() for c in cols
          if (t.rsplit(".", 1)[-1], c) not in existentes]
total = sum(len(v) for v in cat.by_table.values())
print(f"\nB) regra do catálogo sem coluna no Trino: {len(mortas)} de {total}")
for t, c in mortas[:15]:
    print(f"     {t}.{c}")
