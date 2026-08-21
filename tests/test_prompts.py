"""Contrato do diretório `src/rag/prompts/`.

Prompt em arquivo tem um risco que prompt em constante não tinha: o arquivo pode
sumir, ser renomeado ou não ir para a imagem, e o erro só aparece quando alguém
pergunta alguma coisa em produção. Estes testes falham antes disso — no host, sem
Haystack e sem serviço.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag import prompts  # noqa: E402

_DIR = ROOT / "src" / "rag" / "prompts"
_SRC = ROOT / "src"


class TestLoader(unittest.TestCase):
    def test_todo_arquivo_carrega_e_tem_conteudo(self):
        arquivos = sorted(p.stem for p in _DIR.glob("*.md") if p.stem != "README")
        self.assertTrue(arquivos, "nenhum prompt encontrado")
        for nome in arquivos:
            with self.subTest(prompt=nome):
                texto = prompts.load(nome)
                self.assertTrue(texto.strip(), f"{nome} está vazio")
                self.assertFalse(texto.endswith("\n"), "sobrou quebra de linha final")

    def test_prompt_inexistente_diz_quais_existem(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            prompts.load("nao_existe")
        self.assertIn("query_system", str(ctx.exception))

    def test_render_substitui_placeholder(self):
        texto = prompts.render("result_summary_user", query="Q", intent="count", digest="D")
        self.assertIn("Q", texto)
        self.assertIn("count", texto)
        self.assertIn("D", texto)
        self.assertNotIn("{query}", texto)


class TestUsoNoCodigo(unittest.TestCase):
    """Todo `prompts.load("x")`/`render("x")` do código aponta para arquivo real."""

    def _nomes_usados(self):
        usados = set()
        for py in _SRC.rglob("*.py"):
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in {"load", "render"}
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "prompts"
                        and node.args
                        and isinstance(node.args[0], ast.Constant)):
                    usados.add((str(py.relative_to(ROOT)), node.args[0].value))
        return usados

    def test_todos_os_prompts_referenciados_existem(self):
        usados = self._nomes_usados()
        self.assertTrue(usados, "nenhuma chamada a prompts.load/render encontrada")
        for origem, nome in sorted(usados):
            with self.subTest(prompt=nome, origem=origem):
                self.assertTrue((_DIR / f"{nome}.md").is_file(),
                                f"{origem} pede o prompt '{nome}', que não existe")

    def test_nenhum_prompt_ficou_orfao(self):
        """Arquivo que ninguém carrega é prompt morto — ou um `load` esquecido."""
        usados = {nome for _, nome in self._nomes_usados()}
        arquivos = {p.stem for p in _DIR.glob("*.md")} - {"README"}
        self.assertEqual(arquivos - usados, set())

    def test_prompt_de_format_nao_tem_chave_literal(self):
        """`render` usa `str.format`: `{` literal no texto explodiria em runtime."""
        for nome in ("chart_spec_user", "sql_executor_user", "result_summary_user"):
            with self.subTest(prompt=nome):
                prompts.load(nome).format(
                    query="", intent="", columns="", sample="",
                    schema_context="", sql="", digest="",
                )


if __name__ == "__main__":
    unittest.main()
