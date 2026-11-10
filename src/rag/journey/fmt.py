"""Formatação de número para quem não é técnico (módulo puro).

Quatro formas, para quatro usos:

- `exact`    "1.300.452", "R$ 24.956.847.898", "0,061" — o número que a pessoa
             LÊ: frase, cartão em destaque, tabela de valores. Inteiro por
             extenso, sem "mi"/"bi" (pedido de produto, 10/11/2026: "1,3 mi"
             esconde o número de verdade);
- `compact`  "R$ 10,2 bi", "1,3 mil" — só DENTRO do desenho (eixo, rótulo de
             barra, mínimo e máximo do mapa), onde o espaço é pouco. O valor
             exato de cada barra vai na tabela embaixo do gráfico;
- `full`     "R$ 10.234.567,89" — tabela crua e trilha técnica;
- `plain`    "1.283" — para índice, que não se abrevia.
"""
from __future__ import annotations

import math
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


def exact(value: Optional[float], money: bool = False) -> str:
    """O número inteiro, com separador de milhar.

    Dinheiro de mil reais para cima sai sem centavos ("R$ 24.956.847.898"):
    centavo em bilhões é ruído. Abaixo disso, centavos só quando existem.
    Fora de dinheiro, casas decimais só para número quebrado — duas de 1
    para cima ("6,1", "12,35"), três abaixo de 1 ("0,061"), sem zero à
    direita — para que uma taxa pequena não vire "0,1" nem "0"."""
    if value is None:
        return "—"
    v = float(value)
    inteiro = abs(v - round(v)) < 1e-9
    if money:
        casas = 0 if (abs(v) >= 1000 or inteiro) else 2
        return "R$ " + _br(f"{v:,.{casas}f}")
    if inteiro or abs(v) >= 1000:
        return _br(f"{v:,.0f}")
    # De 1 para cima, duas casas; abaixo, três algarismos que contam
    # (0,061 e 0,000412, nunca "0").
    casas = 2 if abs(v) >= 1 else max(3, 2 - math.floor(math.log10(abs(v))))
    texto = _br(f"{v:,.{casas}f}").rstrip("0").rstrip(",")
    return texto if texto not in ("", "-", "-0") else "0"


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
