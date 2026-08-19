"""Amostra de dados da Persona 1 — guarda do SQL, escada de execução e CSV.

O que estes testes protegem, em ordem de gravidade:

1. **Só leitura, com teto.** O SQL da amostra vem de um LLM e roda contra o
   banco analítico. A guarda recusa o que não for uma consulta única de leitura
   e envelopa a consulta para impor o limite de linhas mesmo sem `LIMIT`.
2. **Sempre sai amostra.** Quando a consulta do modelo não roda, a consulta é
   reconstruída do catálogo (sem LLM) — é o degrau que garante dado na tela.
3. **CSV que abre no Excel em português** (BOM + `;`).
"""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:  # pragma: no cover — sqlglot só existe no container
    from rag.components.samples import sample_sql
    HAS_SQLGLOT = True
except ImportError:  # pragma: no cover
    HAS_SQLGLOT = False

try:  # pragma: no cover
    from rag.components.samples.sample_runner import SampleRunner
    HAS_RUNNER = True
except ImportError:  # pragma: no cover
    HAS_RUNNER = False


@unittest.skipUnless(HAS_SQLGLOT, "sqlglot indisponível no host — rode no container")
class TestGuard(unittest.TestCase):
    def test_select_is_wrapped_with_the_row_cap(self):
        sql, recusa = sample_sql.guard("SELECT uf FROM far.mapa", 100)
        self.assertEqual(recusa, "")
        self.assertIn("LIMIT 100", sql)
        self.assertIn("AS amostra", sql)

    def test_cap_wins_over_a_bigger_limit_from_the_llm(self):
        # O envelope é externo: o LIMIT de fora corta, mesmo com LIMIT interno maior.
        sql, _ = sample_sql.guard("SELECT uf FROM far.mapa LIMIT 100000", 10)
        self.assertTrue(sql.rstrip().endswith("LIMIT 10"))

    def test_cte_is_allowed(self):
        sql, recusa = sample_sql.guard("WITH a AS (SELECT 1 AS x) SELECT * FROM a", 10)
        self.assertEqual(recusa, "")
        self.assertTrue(sql)

    def test_write_statement_is_refused(self):
        for danger in ("DELETE FROM far.mapa",
                       "UPDATE far.mapa SET uf = 'SP'",
                       "DROP TABLE far.mapa",
                       "INSERT INTO far.mapa VALUES (1)"):
            sql, recusa = sample_sql.guard(danger, 10)
            self.assertEqual(sql, "", danger)
            self.assertTrue(recusa, danger)

    def test_second_statement_is_refused(self):
        sql, recusa = sample_sql.guard("SELECT 1; DROP TABLE far.mapa", 10)
        self.assertEqual(sql, "")
        self.assertIn("mais de uma", recusa)

    def test_garbage_and_empty_are_refused_with_a_reason(self):
        self.assertTrue(sample_sql.guard("", 10)[1])
        self.assertTrue(sample_sql.guard("isso não é sql", 10)[1])


@unittest.skipUnless(HAS_SQLGLOT, "sqlglot indisponível no host — rode no container")
class TestCatalogFallback(unittest.TestCase):
    CATALOGO = ["empreendimento_far.mapa_nacional", "conjuntura_gold.indicadores"]

    def test_target_table_matches_the_physical_name(self):
        self.assertEqual(
            sample_sql.target_table("SELECT * FROM empreendimento_far.mapa_nacional", self.CATALOGO),
            "empreendimento_far.mapa_nacional",
        )

    def test_target_table_matches_a_bare_table_name(self):
        # O LLM escreve o nome curto; o catálogo guarda `schema.tabela`.
        self.assertEqual(
            sample_sql.target_table("SELECT uf FROM mapa_nacional", self.CATALOGO),
            "empreendimento_far.mapa_nacional",
        )

    def test_unknown_table_has_no_target(self):
        self.assertEqual(sample_sql.target_table("SELECT 1 FROM inventada", self.CATALOGO), "")

    def test_catalog_sql_quotes_schema_and_table(self):
        sql = sample_sql.catalog_sql("far.mapa", ["uf", "valor"], 50)
        self.assertEqual(sql, 'SELECT "uf", "valor" FROM "far"."mapa" LIMIT 50')

    def test_catalog_sql_without_columns_falls_back_to_star(self):
        self.assertIn("SELECT *", sample_sql.catalog_sql("far.mapa", [], 5))


@unittest.skipUnless(HAS_SQLGLOT, "sqlglot indisponível no host — rode no container")
class TestPreviewAndCsv(unittest.TestCase):
    ROWS = [
        {"uf": "SP", "total": 412, "obs": None},
        {"uf": "MG", "total": 118, "obs": None},
    ]

    def test_preview_puts_columns_with_values_first(self):
        columns, rows = sample_sql.preview(["obs", "uf", "total"], self.ROWS, max_columns=2)
        self.assertEqual(columns, ["uf", "total"])
        self.assertEqual(len(rows), 2)

    def test_preview_limits_rows(self):
        _, rows = sample_sql.preview(["uf"], self.ROWS, max_rows=1)
        self.assertEqual(len(rows), 1)

    def test_numeric_column_detection(self):
        self.assertTrue(sample_sql.is_numeric_column("total", self.ROWS))
        self.assertFalse(sample_sql.is_numeric_column("uf", self.ROWS))

    def test_csv_opens_in_excel_pt_br(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = sample_sql.write_csv(f"{tmp}/a.csv", ["uf", "total"], self.ROWS)
            with open(path, "rb") as handle:
                raw = handle.read()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))   # BOM: acento certo
            self.assertIn(b"uf;total", raw)                     # separador pt-BR
            with open(path, encoding="utf-8-sig", newline="") as handle:
                linhas = list(csv.DictReader(handle, delimiter=";"))
            self.assertEqual(linhas[0]["uf"], "SP")


class _FakeExec:
    """Executor roteirizado: mapeia um trecho do SQL para o resultado."""

    def __init__(self, regras: dict):
        self.regras, self.chamadas = regras, []

    def __call__(self, sql, query="", schema="", direct=False):
        self.chamadas.append((sql, direct))
        for trecho, saida in self.regras.items():
            if trecho in sql:
                return saida
        return {"exec_ok": False, "rows": [], "columns": [], "error": "recusado"}


_LINHAS = {"exec_ok": True, "rows": [{"uf": "SP", "total": 412}],
           "columns": ["uf", "total"], "sql_final": "", "error": ""}

_ANSWER = {
    "query": "o que existe sobre habitação?",
    "sample_sql": "SELECT uf, valor_total FROM far.mapa LIMIT 20",
    "tables": [{
        "table": "far.mapa",
        "columns": [{"name": "uf", "type": "text"}, {"name": "total", "type": "int"}],
    }],
}


@unittest.skipUnless(HAS_RUNNER, "dependências indisponíveis no host — rode no container")
class TestSampleLadder(unittest.TestCase):
    def _runner(self, regras: dict) -> tuple[SampleRunner, _FakeExec]:
        runner = SampleRunner(rows=100, preview_rows=10, autocorrect=False)
        fake = _FakeExec(regras)
        runner._exec = fake                       # noqa: SLF001 — injeção no teste
        runner._publish = lambda *a, **k: {"url": "", "file_name": ""}  # noqa: SLF001
        return runner, fake

    def test_llm_sql_that_runs_is_the_one_reported(self):
        runner, _ = self._runner({"valor_total": _LINHAS})
        sample = runner.run(_ANSWER)
        self.assertTrue(sample["ok"])
        self.assertEqual(sample["origin"], "llm")
        self.assertEqual(sample["sql"], _ANSWER["sample_sql"])  # sem o envelope interno
        self.assertEqual(sample["row_count"], 1)

    def test_database_refusal_falls_back_to_the_catalog_query(self):
        runner, fake = self._runner({'"far"."mapa"': _LINHAS})
        sample = runner.run(_ANSWER)
        self.assertTrue(sample["ok"])
        self.assertEqual(sample["origin"], "catalogo")
        self.assertIn('FROM "far"."mapa"', sample["sql"])
        self.assertTrue(fake.chamadas[-1][1])  # o fallback nunca aciona LLM

    def test_sql_refused_by_the_guard_still_yields_a_sample(self):
        runner, fake = self._runner({'"far"."mapa"': _LINHAS})
        sample = runner.run({**_ANSWER, "sample_sql": "DROP TABLE far.mapa"})
        self.assertTrue(sample["ok"])
        self.assertEqual(sample["origin"], "catalogo")
        # a consulta perigosa nunca chegou ao banco
        self.assertTrue(all("DROP" not in sql for sql, _ in fake.chamadas))

    def test_no_rows_is_not_a_sample(self):
        vazio = {"exec_ok": True, "rows": [], "columns": [], "sql_final": "", "error": ""}
        runner, _ = self._runner({"SELECT": vazio})
        sample = runner.run(_ANSWER)
        self.assertFalse(sample["ok"])
        self.assertIn("não devolveu linhas", sample["reason"])

    def test_nothing_runs_degrades_without_raising(self):
        runner, _ = self._runner({})
        sample = runner.run(_ANSWER)
        self.assertFalse(sample["ok"])
        self.assertTrue(sample["reason"])

    def test_no_tables_means_no_sample(self):
        runner, _ = self._runner({})
        self.assertFalse(runner.run({"tables": []})["ok"])


if __name__ == "__main__":
    unittest.main()
