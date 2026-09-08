"""Contrato do mascaramento de dado sensível (ADR-0018).

A pergunta que este arquivo responde a cada mudança é dupla, e as duas metades
importam igualmente:

- o que **tem** de ser mascarado continua sendo (alias, `CAST`, `SELECT *`,
  envelope da amostra, JOIN sem qualificador);
- o que **não pode** ser mascarado continua passando — `COUNT(cpf)`,
  `SUM(valor)`, coluna de tabela que não está no catálogo. Mascarar demais não
  vaza nada, mas destrói o sistema em silêncio: o indicador vira hash e
  ninguém nota até a reunião.

Roda no host, sem Haystack, sem MCP e sem banco: `rag.sensitivity` é função
pura. Só o resolvedor precisa de `sqlglot`.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag.sensitivity import catalog as catalog_mod  # noqa: E402
from rag.sensitivity.catalog import Catalog, parse  # noqa: E402
from rag.sensitivity.mask import REDACTED, apply, plan  # noqa: E402

try:
    import sqlglot  # noqa: F401

    HAS_SQLGLOT = True
except ImportError:  # pragma: no cover
    HAS_SQLGLOT = False

PEPPER = "pimenta-de-teste"

CATALOGO = parse(
    {
        "version": 1,
        "default_strategy": "hash",
        "tables": {
            "fds.cadastro_pj": ["cpf_socio", {"column": "email", "strategy": "redact"}],
            "far.beneficiario": ["nome", "cpf"],
        },
    },
    source="teste",
)


def _plan(sql, columns, catalog=CATALOGO):
    return plan(sql, columns, catalog, dialect="trino")


def _mask(sql, columns, rows, catalog=CATALOGO, scope="global"):
    return apply(rows, _plan(sql, columns, catalog), pepper=PEPPER, scope=scope)


# --------------------------------------------------------------------------- #
class TestCatalogo(unittest.TestCase):
    """O mapeamento nasce fora daqui (PDF, planilha, export) — a leitura tem de
    aceitar as formas em que ele costuma chegar, sem reescrita manual."""

    def test_formato_canonico(self):
        self.assertEqual(CATALOGO.strategy("fds.cadastro_pj", "cpf_socio"), "hash")
        self.assertEqual(CATALOGO.strategy("fds.cadastro_pj", "email"), "redact")

    def test_mapa_cru_sem_chaves_de_topo(self):
        catalog = parse({"far.beneficiario": ["cpf", "nome"]})
        self.assertEqual(catalog.strategy("far.beneficiario", "cpf"), "hash")

    def test_lista_de_registros(self):
        catalog = parse([{"table": "far.beneficiario", "column": "cpf",
                          "strategy": "redact"}])
        self.assertEqual(catalog.strategy("far.beneficiario", "cpf"), "redact")

    def test_fqn_do_openmetadata_cai_para_schema_tabela(self):
        catalog = parse({"Cidades.cidades.far.beneficiario": ["cpf"]})
        self.assertEqual(catalog.strategy("far.beneficiario", "cpf"), "hash")

    def test_acento_e_caixa_nao_importam(self):
        catalog = parse({"FAR.Beneficiário": ["Endereço"]})
        self.assertEqual(catalog.strategy("far.beneficiario", "endereco"), "hash")

    def test_nome_curto_casa_com_schema_tabela(self):
        """O SQL nem sempre qualifica a tabela com o schema."""
        self.assertEqual(CATALOGO.strategy("beneficiario", "cpf"), "hash")

    def test_estrategia_desconhecida_cai_no_default(self):
        catalog = parse({"tables": {"far.b": [{"column": "cpf", "strategy": "xpto"}]}})
        self.assertEqual(catalog.strategy("far.b", "cpf"), "hash")

    def test_catalogo_vazio_e_falsy(self):
        self.assertFalse(parse({}))
        self.assertFalse(Catalog())

    def test_arquivo_ausente_devolve_vazio_sem_levantar(self):
        catalog_mod.clear_cache()
        self.assertFalse(catalog_mod.read("/caminho/que/nao/existe.yml"))


# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAS_SQLGLOT, "sqlglot indisponível no host — rode no container")
class TestPlano(unittest.TestCase):
    """De qual coluna de qual tabela veio cada coluna do resultado."""

    def test_coluna_direta(self):
        p = _plan("SELECT cpf_socio, uf FROM fds.cadastro_pj", ["cpf_socio", "uf"])
        self.assertEqual(p.columns, ["cpf_socio"])

    def test_alias_segue_a_origem(self):
        p = _plan("SELECT cpf_socio AS documento FROM fds.cadastro_pj", ["documento"])
        self.assertEqual(p.columns, ["documento"])

    def test_expressao_escalar_carrega_a_sensibilidade(self):
        """`CAST(cpf AS VARCHAR)` continua sendo o CPF."""
        p = _plan("SELECT CAST(cpf AS VARCHAR) AS doc FROM far.beneficiario", ["doc"])
        self.assertEqual(p.columns, ["doc"])

    def test_agregacao_nao_e_dado_pessoal(self):
        """A regra que salva o sistema: contar pessoas não identifica ninguém."""
        p = _plan(
            "SELECT uf, COUNT(DISTINCT cpf) AS pessoas FROM far.beneficiario GROUP BY uf",
            ["uf", "pessoas"],
        )
        self.assertEqual(p.columns, [])

    def test_tabela_fora_do_catalogo_passa_limpo(self):
        p = _plan("SELECT nome, uf FROM conjuntura_gold.municipio", ["nome", "uf"])
        self.assertEqual(p.columns, [])

    def test_select_star_confere_pelo_nome(self):
        p = _plan("SELECT * FROM far.beneficiario", ["cpf", "nome", "uf"])
        self.assertEqual(sorted(p.columns), ["cpf", "nome"])

    def test_envelope_da_amostra_e_desembrulhado(self):
        """`sample_sql.guard` envelopa a consulta; a projeção de fora é `*`."""
        sql = ("SELECT * FROM (SELECT cpf_socio AS doc, uf FROM fds.cadastro_pj) "
               "AS amostra LIMIT 100")
        p = _plan(sql, ["doc", "uf"])
        self.assertEqual(p.columns, ["doc"])

    def test_join_sem_qualificador_fecha_na_duvida(self):
        sql = ("SELECT cpf, valor FROM far.beneficiario b "
               "JOIN conjuntura_gold.repasse r ON r.id = b.id")
        p = _plan(sql, ["cpf", "valor"])
        self.assertEqual(p.columns, ["cpf"])

    def test_qualificador_por_apelido_resolve_a_tabela(self):
        sql = "SELECT b.cpf, m.nome FROM far.beneficiario b JOIN conjuntura_gold.municipio m ON 1=1"
        p = _plan(sql, ["cpf", "nome"])
        # `m.nome` é de uma tabela FORA do catálogo: o apelido evita o falso
        # positivo que o casamento por nome puro produziria.
        self.assertEqual(p.columns, ["cpf"])

    def test_sql_impossivel_de_parsear_ainda_protege(self):
        p = _plan("isto não é SQL {{{", ["cpf"])
        self.assertEqual(p.columns, [])  # sem tabela conhecida, nada a fazer

    def test_regra_global_por_nome_protege_sql_ilegivel(self):
        catalog = parse({"columns": ["cpf"]})
        p = _plan("isto não é SQL {{{", ["cpf", "uf"], catalog)
        self.assertEqual(p.columns, ["cpf"])

    def test_catalogo_vazio_nao_produz_plano(self):
        self.assertFalse(_plan("SELECT cpf FROM far.beneficiario", ["cpf"], Catalog()))


# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAS_SQLGLOT, "sqlglot indisponível no host — rode no container")
class TestMascaramento(unittest.TestCase):
    """O valor que sai da célula."""

    SQL = "SELECT cpf_socio, email, uf FROM fds.cadastro_pj"
    COLS = ["cpf_socio", "email", "uf"]
    ROWS = [
        {"cpf_socio": "12345678901", "email": "a@b.com", "uf": "BA"},
        {"cpf_socio": "12345678901", "email": "c@d.com", "uf": "SP"},
        {"cpf_socio": "98765432100", "email": None, "uf": "BA"},
    ]

    def test_hash_substitui_e_marca(self):
        out = _mask(self.SQL, self.COLS, self.ROWS)
        self.assertNotIn("12345678901", str(out))
        self.assertTrue(out[0]["cpf_socio"].startswith("#"))

    def test_hash_e_estavel_e_preserva_distintos(self):
        """É o que mantém COUNT(DISTINCT) e GROUP BY significando algo."""
        out = _mask(self.SQL, self.COLS, self.ROWS)
        self.assertEqual(out[0]["cpf_socio"], out[1]["cpf_socio"])
        self.assertNotEqual(out[0]["cpf_socio"], out[2]["cpf_socio"])

    def test_redact_nao_pseudonimiza(self):
        out = _mask(self.SQL, self.COLS, self.ROWS)
        self.assertEqual(out[0]["email"], REDACTED)

    def test_coluna_nao_sensivel_fica_intacta(self):
        out = _mask(self.SQL, self.COLS, self.ROWS)
        self.assertEqual([r["uf"] for r in out], ["BA", "SP", "BA"])

    def test_nulo_continua_nulo(self):
        """Ausência de dado não é dado pessoal — e um pseudônimo para `None`
        criaria uma categoria fantasma no gráfico e no digest."""
        out = _mask(self.SQL, self.COLS, self.ROWS)
        self.assertIsNone(out[2]["email"])

    def test_forma_do_resultado_nao_muda(self):
        out = _mask(self.SQL, self.COLS, self.ROWS)
        self.assertEqual(len(out), len(self.ROWS))
        self.assertEqual(list(out[0].keys()), list(self.ROWS[0].keys()))

    def test_nao_muta_a_entrada(self):
        original = [dict(r) for r in self.ROWS]
        _mask(self.SQL, self.COLS, self.ROWS)
        self.assertEqual(self.ROWS, original)

    def test_sem_segredo_nao_gera_hash(self):
        """Hash de CPF sem segredo é reversível por força bruta: degrada para
        redação em vez de fingir proteção."""
        out = apply(self.ROWS, _plan(self.SQL, self.COLS), pepper="", scope="global")
        self.assertEqual(out[0]["cpf_socio"], REDACTED)

    def test_pimenta_diferente_muda_o_pseudonimo(self):
        a = apply(self.ROWS, _plan(self.SQL, self.COLS), pepper="A")
        b = apply(self.ROWS, _plan(self.SQL, self.COLS), pepper="B")
        self.assertNotEqual(a[0]["cpf_socio"], b[0]["cpf_socio"])

    def test_escopo_column_impede_correlacao(self):
        sql = "SELECT cpf, nome FROM far.beneficiario"
        rows = [{"cpf": "111", "nome": "111"}]
        glob = _mask(sql, ["cpf", "nome"], rows, scope="global")
        col = _mask(sql, ["cpf", "nome"], rows, scope="column")
        self.assertEqual(glob[0]["cpf"], glob[0]["nome"])
        self.assertNotEqual(col[0]["cpf"], col[0]["nome"])


# --------------------------------------------------------------------------- #
try:  # pragma: no cover — depende do ambiente
    from rag.components.answer.sql_answer_builder import render_masking_note
    from rag.components.charts.chart_spec import ChartSpecBuilder
    from rag.components.executors.mcp_sql_executor import McpSqlExecutor

    HAYSTACK = True
except ImportError:  # pragma: no cover
    HAYSTACK = False


@unittest.skipUnless(HAS_SQLGLOT and HAYSTACK, "Haystack/sqlglot indisponíveis no host")
class TestIntegracaoNoExecutor(unittest.TestCase):
    """O gancho no `McpSqlExecutor` — o ponto único por onde toda linha passa.

    Testar o `mask` isolado prova que a função sabe mascarar; só isto prova que
    ela é CHAMADA no caminho de saída. É a diferença entre ter a proteção e
    achar que se tem."""

    def setUp(self):
        import json
        import tempfile

        from rag.settings.configs import settings

        self.settings = settings
        self._antes = {
            k: getattr(settings, k)
            for k in ("SENSITIVE_MASKING_ENABLED", "SENSITIVE_MAP_PATH",
                      "SENSITIVE_HASH_PEPPER", "SENSITIVE_MASK_DRY_RUN")
        }
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump({"tables": {"far.beneficiario": ["cpf"]}}, handle)
        handle.close()
        self.path = handle.name
        catalog_mod.clear_cache()
        settings.SENSITIVE_MASKING_ENABLED = True
        settings.SENSITIVE_MAP_PATH = self.path
        settings.SENSITIVE_HASH_PEPPER = PEPPER
        settings.SENSITIVE_MASK_DRY_RUN = False

    def tearDown(self):
        import os

        for key, value in self._antes.items():
            setattr(self.settings, key, value)
        catalog_mod.clear_cache()
        os.unlink(self.path)

    def _protect(self, rows, columns, sql):
        return McpSqlExecutor.__dict__["_protect"](
            McpSqlExecutor.__new__(McpSqlExecutor), rows, columns, sql
        )

    def test_saida_do_executor_ja_vem_mascarada(self):
        out = self._protect(
            [{"cpf": "111", "uf": "BA"}], ["cpf", "uf"],
            "SELECT cpf, uf FROM far.beneficiario",
        )
        self.assertNotEqual(out["rows"][0]["cpf"], "111")
        self.assertEqual(out["rows"][0]["uf"], "BA")
        self.assertEqual(out["masked_columns"], ["cpf"])
        self.assertTrue(out["exec_ok"])

    def test_dry_run_loga_sem_alterar(self):
        self.settings.SENSITIVE_MASK_DRY_RUN = True
        out = self._protect(
            [{"cpf": "111"}], ["cpf"], "SELECT cpf FROM far.beneficiario"
        )
        self.assertEqual(out["rows"][0]["cpf"], "111")
        self.assertEqual(out["masked_columns"], [])

    def test_desligado_nao_toca_em_nada(self):
        self.settings.SENSITIVE_MASKING_ENABLED = False
        out = self._protect(
            [{"cpf": "111"}], ["cpf"], "SELECT cpf FROM far.beneficiario"
        )
        self.assertEqual(out["rows"][0]["cpf"], "111")

    def test_autocorrecao_inibida_em_tabela_sensivel(self):
        """A autocorreção devolve o retorno da tool ao contexto do modelo — é
        por ali que dado pessoal entraria num prompt."""
        self.assertTrue(
            McpSqlExecutor._sensitive_sql("SELECT cpf FROM far.beneficiario")
        )
        self.assertFalse(
            McpSqlExecutor._sensitive_sql("SELECT uf FROM conjuntura_gold.municipio")
        )

    def test_coluna_mascarada_nao_vira_eixo_do_grafico(self):
        rows = [{"cpf": "#abc", "total": 10}, {"cpf": "#def", "total": 20}]
        spec = ChartSpecBuilder().run(
            query="", columns=["cpf", "total"], rows=rows, masked_columns=["cpf"]
        )["spec"]
        self.assertEqual(spec["chart_type"], "none")


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host")
class TestNotaDeTransparencia(unittest.TestCase):
    """Sem a nota, `#a3f9c1d20b` é lido como defeito do sistema."""

    def test_silenciosa_quando_nada_foi_mascarado(self):
        self.assertEqual(render_masking_note([]), "")

    def test_cita_as_colunas_e_explica_a_estabilidade(self):
        nota = render_masking_note(["cpf", "nome"])
        self.assertIn("`cpf`", nota)
        self.assertIn("`nome`", nota)
        self.assertIn("estável", nota)


if __name__ == "__main__":
    unittest.main()
