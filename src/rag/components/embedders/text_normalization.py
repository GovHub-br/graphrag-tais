"""Normalização de texto para o canal ESPARSO (BM25).

Medido em 2026-08-13, com o stemmer português do fastembed:

    'situação'        -> 'situaçã'        |  'situacao'        -> 'situaca'
    'execução física' -> 'execu'          |  'execucao_fisica' -> 'execucao_fis'

Ou seja: **acento quebra o casamento lexical**, e underscore não separa tokens.
Isso não é detalhe — o corpus do MCid mistura descrições acentuadas e não
acentuadas (a mesma ideia aparece como "execução" num card e "execucao" noutro),
e os VALORES do banco são quase todos sem acento enquanto as perguntas dos
usuários vêm acentuadas.

A correção é dobrar acento e separar underscore **dos dois lados** — documento e
query. Aplicar só num deles piora: os tokens deixariam de viver no mesmo espaço.

Só afeta o vetor esparso. O texto do card e o embedding denso ficam intactos —
o denso lida bem com acento e não queremos degradar a semântica.
"""
from __future__ import annotations

import re
import unicodedata

_UNDERSCORE = re.compile(r"[_]+")


def fold(text: str) -> str:
    """Remove acentos e troca underscore por espaço, preservando o resto.

    'execução_física' -> 'execucao fisica'
    """
    if not text:
        return text
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _UNDERSCORE.sub(" ", without_marks)
