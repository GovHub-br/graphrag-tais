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


_BULLET = re.compile(r"^\s*[-*+•]\s+(?=\S)")
_ROTULO_TRAVESSAO = re.compile(r"^(\*\*[^*]+\*\*)\s+[—–]\s+")     # "**Lei** — x" → "**Lei**: x"
_TRAVESSAO = re.compile(r"\s+[—–]\s+|\s+-\s+(?=\S)")               # " — ", " – ", " - "
_PONTO_MEDIO = re.compile(r"\s*·\s*")
_FENCE = re.compile(r"^\s*(```|~~~)")
# Dentro de célula, só entre palavras: "Prata — série" vira "Prata, série",
# mas a célula vazia "| — |" fica.
_CELULA = re.compile(r"(?<=[\w)])\s+[—–·]\s+(?=[^\s|])")


def _prose_line(line: str) -> str:
    """Uma linha de prosa, sem tocar no que está entre crases."""
    pedacos = line.split("`")
    for i in range(0, len(pedacos), 2):          # pares = fora de código inline
        trecho = pedacos[i]
        if i == 0:
            trecho = _ROTULO_TRAVESSAO.sub(r"\1: ", trecho)
        trecho = _TRAVESSAO.sub(", ", trecho)
        trecho = _PONTO_MEDIO.sub(", ", trecho)
        pedacos[i] = trecho
    return "`".join(pedacos)


def _table_line(line: str) -> str:
    pedacos = line.split("`")
    for i in range(0, len(pedacos), 2):
        pedacos[i] = _CELULA.sub(", ", pedacos[i])
    return "`".join(pedacos)


def plain_typography(text: str) -> str:
    """Tira da resposta a tipografia que soa a texto de máquina: marcador de
    lista ("- item"), travessão separando ideias (" — ") e ponto médio (" · ")
    (pedido de produto, 10/11/2026).

    Item de lista vira parágrafo próprio (linha em branco entre eles, senão o
    Markdown junta tudo numa linha só). Não toca em bloco de código, linha de
    tabela, imagem, link sozinho na linha, comentário de estado (`[//]: #`),
    régua (`---`) nem no que está entre crases: lá o hífen é SQL, data ou
    separador de tabela. Em linha de tabela, só o travessão e o ponto médio
    ENTRE palavras saem; a célula vazia ("—") fica."""
    if not text:
        return text
    saida: list = []
    em_codigo = False
    era_item = False
    for linha in text.split("\n"):
        if _FENCE.match(linha):
            em_codigo = not em_codigo
            saida.append(linha)
            era_item = False
            continue
        crua = linha.strip()
        if not em_codigo and crua.startswith("|"):
            saida.append(_table_line(linha))
            era_item = False
            continue
        if (em_codigo or not crua or crua.startswith(("![", "[//]:", "<"))
                or set(crua) <= {"-", "*", "_", " "}):
            saida.append(linha)
            era_item = False
            continue
        item = bool(_BULLET.match(linha))
        if item:
            linha = _BULLET.sub("", linha)
            if saida and saida[-1].strip():
                saida.append("")                 # parágrafo próprio
        elif era_item and saida and saida[-1].strip():
            saida.append("")
        saida.append(_prose_line(linha))
        era_item = item
    return "\n".join(saida)


def strip_emoji(text: str) -> str:
    """Tira emoji do texto mostrado ao usuário — o Assistente não usa emoji
    (ADR-0020). Leva junto UM espaço logo depois, para "📥 Baixar" virar
    "Baixar" sem deixar espaço sobrando; indentação de código não é tocada."""
    return _EMOJI.sub("", text) if text else text
