"""Pedidos de bastidor do OpenWebUI, respondidos sem LLM (módulo puro).

Depois de cada resposta, o OpenWebUI faz chamadas extras ao MESMO modelo para
gerar o título da conversa, as tags e as sugestões de continuação (follow-ups).
Para um modelo do Hayhooks, essas chamadas chegam ao `run_chat_completion` como
se fossem uma pergunta qualquer — um texto que começa com `### Task:` e traz o
histórico entre `<chat_history>`. Sem tratamento, cada uma rodaria o RAG inteiro
sobre o texto da instrução.

Na jornada (ADR-0020), os follow-ups SÃO os botões das personas, então essa
chamada é exatamente o gancho de que a interface precisa: a resposta é
determinística, montada a partir do marcador escondido na última resposta
(`journey.marker`). Título e tags também saem sem LLM.

Os textos são reconhecidos pelos templates padrão do OpenWebUI 0.11
(`config.py`: `DEFAULT_*_GENERATION_PROMPT_TEMPLATE`). Se alguém configurar um
template próprio, ele precisa manter o cabeçalho `### Task:` e a chave do JSON
de saída (`follow_ups`, `title`, `tags`), que é o que se reconhece aqui.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional

FOLLOW_UPS = "follow_ups"
TITLE = "title"
TAGS = "tags"
OTHER = "other"

_CABECALHO = "### Task:"
_PRIMEIRA_PERGUNTA = re.compile(r"^USER:\s*(.+)$", re.MULTILINE)
_TITULO_MAX = 60


def detect(text: str) -> Optional[str]:
    """Tipo do pedido de bastidor, ou None se for uma mensagem de verdade."""
    texto = (text or "").lstrip()
    if not texto.startswith(_CABECALHO):
        return None
    if '"follow_ups"' in texto:
        return FOLLOW_UPS
    if '"title"' in texto:
        return TITLE
    if '"tags"' in texto:
        return TAGS
    return OTHER


def chat_history(text: str) -> str:
    """O trecho entre `<chat_history>` e `</chat_history>` (ou o texto todo)."""
    inicio, fim = text.find("<chat_history>"), text.rfind("</chat_history>")
    if inicio == -1 or fim == -1:
        return text
    return text[inicio + len("<chat_history>"):fim]


def first_question(text: str) -> str:
    achado = _PRIMEIRA_PERGUNTA.search(chat_history(text))
    return achado.group(1).strip() if achado else ""


def title_for(text: str) -> str:
    """Título da conversa = a primeira pergunta, encurtada em fronteira de palavra.

    O template pede "2 a 4 palavras", mas a pergunta da pessoa é um título melhor
    do que um resumo inventado — e não custa uma chamada de LLM."""
    pergunta = first_question(text).rstrip("?!. ")
    if len(pergunta) <= _TITULO_MAX:
        return pergunta or "Nova conversa"
    corte = pergunta[:_TITULO_MAX].rsplit(" ", 1)[0]
    return corte + "…"


def json_follow_ups(buttons: List[str]) -> str:
    return json.dumps({"follow_ups": list(buttons)}, ensure_ascii=False)


def json_title(text: str) -> str:
    return json.dumps({"title": title_for(text)}, ensure_ascii=False)


def json_tags() -> str:
    return json.dumps({"tags": ["Dados públicos"]}, ensure_ascii=False)
