"""Embedder de query com prefixo de instrução (ADR-0009 / plano item 1.3).

A família GTE-Qwen2-instruct e a Qwen3-Embedding são *instruction-tuned* e
documentam uma convenção **assimétrica**:

- **query**    → ``Instruct: {tarefa}\\nQuery: {pergunta}``
- **documento** → texto cru, sem prefixo

A instrução informa ao modelo qual noção de relevância usar. Sem ela a pergunta
é embutida com a mesma convenção de um documento, e a busca passa a perguntar
"qual card se PARECE com este texto?" em vez de "qual card RESPONDE a ele?".

Como só o lado da query muda, **não há reindexação**: os vetores dos documentos
continuam válidos. Por isso o wrapper é ligado/desligado por variável de
ambiente, e o default é DESLIGADO (comportamento atual preservado).

Medição em 2026-08-11 (canal denso isolado, 8 casos do gold `draft`): com o
gte-qwen2 o prefixo em **inglês** rendeu +25% de sementes-gold sem piorar
contaminação nem table recall; em **português** rendeu o mesmo ganho mas piorou
os dois. Com o qwen3-embedding:4b o efeito foi negativo. Ou seja: o ganho é
dependente do modelo e precisa ser re-medido a cada troca — nunca assumido.
"""
from typing import Any, Dict, List

from haystack import component
from haystack_integrations.components.embedders.ollama import OllamaTextEmbedder

from rag.settings.configs import settings


@component
class InstructedTextEmbedder:
    """Prefixa a instrução na query e delega ao embedder denso."""

    def __init__(self, inner: OllamaTextEmbedder, task: str | None = None) -> None:
        self._inner = inner
        self.task = task if task is not None else settings.QUERY_INSTRUCTION_TASK

    def warm_up(self) -> None:
        warm = getattr(self._inner, "warm_up", None)
        if callable(warm):
            warm()

    @component.output_types(embedding=List[float], meta=Dict[str, Any])
    def run(self, text: str) -> Dict[str, Any]:
        prompt = f"Instruct: {self.task}\nQuery: {text}" if self.task else text
        return self._inner.run(text=prompt)
