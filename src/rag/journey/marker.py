"""Estado escondido na resposta — a conversa é a memória da jornada (módulo puro).

Quando a pessoa clica num botão, o OpenWebUI manda o histórico inteiro de volta,
mas não manda nenhum identificador de conversa para o modelo (o `metadata` é
removido antes de a requisição sair). Então cada resposta leva, no fim, uma
linha que o Markdown **não renderiza**:

    [//]: # (tais:v1 ref=6f1c2a9b rota=numero trilha=populacao vistas=tecnica)

É uma definição de link de referência sem uso — sintaxe padrão de comentário em
Markdown, invisível no chat. Ela carrega o necessário para o próximo passo:

- `ref`    chave do resultado guardado (`journey.state`), para a trilha não
           recalcular o número e não arriscar um SQL diferente do primeiro;
- `rota`   numero | assunto;
- `trilha` o que esta resposta foi;
- `vistas` trilhas já abertas para este `ref` (para não repetir botão).

O mesmo texto chega no prompt de follow-up do OpenWebUI (o histórico vai junto),
e é de lá que os botões certos são escolhidos sem chamar LLM nenhum.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

_VERSAO = "tais:v1"
_PADRAO = re.compile(r"tais:v1 ([^)\n]*)")
_PAR = re.compile(r"(\w+)=([\w,.-]*)")


@dataclass
class Marker:
    ref: str = ""
    rota: str = ""
    trilha: str = ""
    vistas: List[str] = field(default_factory=list)

    def render(self) -> str:
        partes = [f"ref={self.ref}", f"rota={self.rota}", f"trilha={self.trilha}"]
        if self.vistas:
            partes.append("vistas=" + ",".join(self.vistas))
        return f"[//]: # ({_VERSAO} {' '.join(partes)})"


def parse(text: str) -> Optional[Marker]:
    """O ÚLTIMO marcador do texto (o mais recente), ou None."""
    achados = _PADRAO.findall(text or "")
    if not achados:
        return None
    campos = dict(_PAR.findall(achados[-1]))
    vistas = [v for v in campos.get("vistas", "").split(",") if v]
    return Marker(ref=campos.get("ref", ""), rota=campos.get("rota", ""),
                  trilha=campos.get("trilha", ""), vistas=vistas)


def last_in_messages(messages: Iterable[dict]) -> Optional[Marker]:
    """Marcador da resposta mais recente do assistente no histórico."""
    for message in reversed(list(messages or [])):
        if message.get("role") != "assistant":
            continue
        found = parse(content_of(message))
        if found:
            return found
    return None


def content_of(message: dict) -> str:
    content = message.get("content", "")
    if isinstance(content, list):  # formato multimodal da API da OpenAI
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""
