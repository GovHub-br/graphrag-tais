"""Sessão MCP do agente de autocorreção do `McpSqlExecutor`.

O bug que estes testes fixam: o `Agent` do haystack-ai 3 faz `toolset.spawn()`
(cópia rasa) a cada `run`; a cópia herdava o `_worker` do `MCPToolset` e, no
`__del__` do fim do `run`, fechava a sessão do toolset ORIGINAL. Só a primeira
autocorreção de cada processo funcionava — as seguintes falhavam com "Not
connected to an MCP server" (eval da jornada, 09/11/2026).

Não sobe MCP: o worker é um dublê que conta quantas vezes foi parado.
"""
from __future__ import annotations

import gc
import unittest

try:  # pragma: no cover - depende do ambiente
    from rag.components.executors import mcp_sql_executor as mod
    HAYSTACK = True
except ImportError:  # pragma: no cover
    HAYSTACK = False


class _Client:
    def __init__(self, vivo: bool = True) -> None:
        self.session = object() if vivo else None


class _Worker:
    def __init__(self, vivo: bool = True) -> None:
        self._client = _Client(vivo)
        self.paradas = 0

    def stop(self) -> None:
        self.paradas += 1
        self._client.session = None


def _toolset(worker):
    """Toolset já "conectado", sem passar pelo `__init__` (que abriria a sessão)."""
    ts = object.__new__(mod._toolset_cls())
    ts.tools = []
    ts._is_warmed_up = True
    ts._warmup_called = True
    ts._selected_tool_names = None
    ts._worker = worker
    return ts


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestSpawnNaoFechaASessao(unittest.TestCase):
    def test_copia_do_run_descartada_nao_derruba_a_sessao(self):
        worker = _Worker()
        original = _toolset(worker)
        copia = original.spawn()          # o que o Agent.run faz
        del copia
        gc.collect()                      # fim do Agent.run
        self.assertEqual(worker.paradas, 0)
        self.assertTrue(mod._session_alive(original))

    def test_original_continua_dono_da_sessao(self):
        worker = _Worker()
        original = _toolset(worker)
        original.close()
        self.assertEqual(worker.paradas, 1)
        self.assertFalse(mod._session_alive(original))


class _Agente:
    def __init__(self, toolset) -> None:
        self.tools = toolset


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestGetAgentReconecta(unittest.TestCase):
    def _executor(self, agente_atual):
        ex = mod.McpSqlExecutor()
        ex._agent = agente_atual
        novos = []

        def build():
            novos.append(_Agente(_toolset(_Worker())))
            return novos[-1]

        ex._build_agent = build
        return ex, novos

    def test_sessao_viva_reusa_o_agente(self):
        atual = _Agente(_toolset(_Worker()))
        ex, novos = self._executor(atual)
        self.assertIs(ex._get_agent(), atual)
        self.assertEqual(novos, [])

    def test_sessao_morta_reconstroi(self):
        atual = _Agente(_toolset(_Worker(vivo=False)))
        ex, novos = self._executor(atual)
        self.assertIs(ex._get_agent(), novos[0])


if __name__ == "__main__":
    unittest.main()
