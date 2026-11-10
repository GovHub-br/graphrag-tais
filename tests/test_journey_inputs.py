"""Inputs das pipelines reusadas pela Jornada, com TODAS as flags opcionais.

Por que existe: o `product_router` (SEED_PRODUCT_ROUTING) entrou na pipeline de
query num PR, a Jornada entrou noutro, e cada um passou na suíte sozinho. Juntos,
o Assistente respondia "Missing mandatory input 'query' for component
'product_router'" — porque a Jornada montava os inputs do motor com uma cópia
própria, e a suíte roda nos DEFAULTS, em que o roteamento vem desligado.

Aqui as pipelines são CONSTRUÍDAS de verdade (sem rodar: construir não conecta
em Qdrant, Neo4j nem Ollama) com cada combinação de componentes opcionais, e se
cobra que os inputs montados pelos TRÊS wrappers — Indicador, Explorar e Jornada
— cubram todo input obrigatório que o grafo deixou sem conexão. Componente
opcional novo que exija input entra aqui sozinho: o teste lê o grafo.

Precisa do Haystack (container):

    docker exec -w /app -e PYTHONPATH=/app:/app/src hayhooks \\
        python -m unittest tests.test_journey_inputs
"""
from __future__ import annotations

import itertools
import unittest
from unittest import mock

try:  # pragma: no cover - depende do ambiente
    from rag.pipelines.explore import pipeline_wrapper as explore_wrapper
    from rag.pipelines.explore.explore_pipeline import build_explore_pipeline
    from rag.pipelines.jornada import pipeline_wrapper as jornada_wrapper
    from rag.pipelines.query import pipeline_wrapper as query_wrapper
    from rag.pipelines.query.query_pipeline import build_query_pipeline
    from rag.settings.configs import settings

    OK = True
except ImportError:  # pragma: no cover
    OK = False

_Q = "Qual o total contratado por unidade da federação?"


def _missing(pipeline, inputs):
    """Inputs obrigatórios e sem conexão que `inputs` não preenche."""
    faltando = []
    for comp, sockets in pipeline.inputs().items():
        for nome, meta in sockets.items():
            if meta.get("is_mandatory") and nome not in inputs.get(comp, {}):
                faltando.append(f"{comp}.{nome}")
    return faltando


class _Captura(Exception):
    """Interrompe o `pipeline.run` do wrapper e carrega os inputs que ele montou."""

    def __init__(self, inputs):
        super().__init__("captura")
        self.inputs = inputs


def _inputs_do_wrapper(cls, pipeline, attr="pipeline", **kw):
    """Roda o `_run` de um wrapper sem `setup`, capturando os inputs do `run`."""
    wrapper = cls.__new__(cls)
    setattr(wrapper, attr, pipeline)

    def _run(inputs, **_):
        raise _Captura(inputs)

    with mock.patch.object(pipeline, "run", _run):
        try:
            wrapper._run(_Q, **kw)
        except _Captura as cap:
            return cap.inputs
    raise AssertionError("o wrapper não chamou pipeline.run")


@unittest.skipUnless(OK, "precisa do Haystack (rode no container)")
class MotorInputsTest(unittest.TestCase):
    """Pipeline de query: roteamento por produto × rerank."""

    def test_todas_as_combinacoes(self):
        for routing, rerank in itertools.product([False, True], repeat=2):
            with self.subTest(routing=routing, rerank=rerank), \
                 mock.patch.object(settings, "SEED_PRODUCT_ROUTING", routing), \
                 mock.patch.object(settings, "RERANK_ENABLED", rerank):
                pipe = build_query_pipeline()
                self.assertEqual("product_router" in pipe.graph.nodes, routing)
                jornada = jornada_wrapper.motor_inputs(pipe, _Q)
                self.assertEqual(_missing(pipe, jornada), [], "Jornada")
                indicador = _inputs_do_wrapper(query_wrapper.PipelineWrapper, pipe)
                self.assertEqual(_missing(pipe, indicador), [], "Indicador")
                # A Jornada usa o motor igual ao Indicador: mesmo dict.
                self.assertEqual(jornada, indicador)


@unittest.skipUnless(OK, "precisa do Haystack (rode no container)")
class ExploreInputsTest(unittest.TestCase):
    """Pipeline do Explorar: roteamento por produto ligado e desligado."""

    def test_com_e_sem_roteamento(self):
        for routing in (False, True):
            with self.subTest(routing=routing), \
                 mock.patch.object(settings, "EXPLORE_PRODUCT_ROUTING", routing):
                pipe = build_explore_pipeline()
                self.assertEqual("product_router" in pipe.graph.nodes, routing)
                jornada = jornada_wrapper.explore_inputs(pipe, _Q)
                self.assertEqual(_missing(pipe, jornada), [], "Jornada")
                explorar = _inputs_do_wrapper(explore_wrapper.PipelineWrapper, pipe)
                self.assertEqual(_missing(pipe, explorar), [], "Explorar")
                self.assertEqual(jornada, explorar)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
