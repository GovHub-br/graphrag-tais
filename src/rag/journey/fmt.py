"""Formatação de número para quem não é técnico (módulo puro).

Duas formas, para dois usos:

- `compact`  "R$ 10,2 bi", "1,3 mil", "R$ 48,20" — para frase, cartão e rótulo;
- `full`     "R$ 10.234.567,89" — para tabela e trilha técnica, onde o número
             exato importa;
- `plain`    "1.283" — para índice, que não se abrevia.
"""
from __future__ import annotations

import re
from typing import Optional

_ESCALAS = ((1e12, "tri"), (1e9, "bi"), (1e6, "mi"), (1e3, "mil"))


def _br(texto: str) -> str:
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def full(value: Optional[float], money: bool = False, decimals: Optional[int] = None) -> str:
    if value is None:
        return "—"
    v = float(value)
    if decimals is None:
        decimals = 2 if (money or abs(v - round(v)) > 1e-9) else 0
    texto = _br(f"{v:,.{decimals}f}")
    return f"R$ {texto}" if money else texto


def compact(value: Optional[float], money: bool = False) -> str:
    if value is None:
        return "—"
    v = float(value)
    for div, sufixo in _ESCALAS:
        if abs(v) >= div:
            numero = v / div
            casas = 0 if abs(numero) >= 100 else 1
            texto = _br(f"{numero:.{casas}f}")
            if texto.endswith(",0"):
                texto = texto[:-2]
            return (f"R$ {texto} {sufixo}" if money else f"{texto} {sufixo}")
    if money:
        # Centavos só quando existem: "R$ 820" e "R$ 491,58", não "R$ 820,00".
        casas = 0 if abs(v - round(v)) < 1e-9 else 2
        return "R$ " + _br(f"{v:,.{casas}f}")
    return full(v, decimals=0 if abs(v - round(v)) < 1e-9 else 1)


def plain(value: Optional[float]) -> str:
    """Número por extenso, sem "mil"/"mi": para índice, onde "1,3 mil" não quer
    dizer nada. Uma casa decimal abaixo de 1.000, nenhuma acima."""
    if value is None:
        return "—"
    v = float(value)
    texto = full(v, decimals=0 if abs(v) >= 1000 else 1)
    return texto[:-2] if texto.endswith(",0") else texto


def percent(fraction: float, decimals: int = 0) -> str:
    return _br(f"{fraction * 100:.{decimals}f}") + "%"


_PREFIXOS = re.compile(r"^(sum|total|avg|count|qtd|qt|vl|vlr|nr|nu)_")


def humanize(column: str) -> str:
    """`total_valor_contratado` → `valor contratado`; nome de coluna legível."""
    nome = (column or "").strip().lower()
    nome = _PREFIXOS.sub("", nome)
    nome = nome.replace("_", " ").strip()
    return nome or column


_MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
_ANO_MES = re.compile(r"^(\d{4})-(\d{2})(?:-\d{2})?(?:[T ].*)?$")


def period(label: str) -> str:
    """`2026-06` (ou `2026-06-01`) → `jun/2026`; o resto passa como veio."""
    achado = _ANO_MES.match(str(label or "").strip())
    if not achado:
        return str(label)
    mes = int(achado.group(2))
    return f"{_MESES[mes - 1]}/{achado.group(1)}" if 1 <= mes <= 12 else str(label)
