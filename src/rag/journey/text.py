"""Normalização de texto compartilhada pela jornada (módulo puro)."""
from __future__ import annotations

import re
import unicodedata

_NAO_PALAVRA = re.compile(r"[^0-9a-z ]+")
_ESPACOS = re.compile(r"\s+")


def fold(text: str) -> str:
    """Minúsculas, sem acento, sem emoji e sem pontuação, espaços colapsados.

    É a forma canônica para comparar o texto de um botão clicado com o rótulo
    que o gerou: o OpenWebUI devolve o rótulo exatamente como foi mostrado,
    emoji incluído, mas a pessoa também pode digitar o mesmo pedido à mão.
    """
    sem_acento = unicodedata.normalize("NFKD", text or "")
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    limpo = _NAO_PALAVRA.sub(" ", sem_acento.lower())
    return _ESPACOS.sub(" ", limpo).strip()


# Pictogramas e símbolos coloridos (emoji), o seletor de variação e o ZWJ.
# Setas (→), travessão e "·" são tipografia, não emoji, e ficam.
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2139"
    "\u231A-\u23FF\uFE0F\u200D]+ ?"
)


def strip_emoji(text: str) -> str:
    """Tira emoji do texto mostrado ao usuário — o Assistente não usa emoji
    (ADR-0020). Leva junto UM espaço logo depois, para "📥 Baixar" virar
    "Baixar" sem deixar espaço sobrando; indentação de código não é tocada."""
    return _EMOJI.sub("", text) if text else text
