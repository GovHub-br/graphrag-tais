"""Municípios do IBGE: nome ↔ código e a malha de cada estado (módulo puro).

O mapa municipal da jornada (`interactive.page`, `infographics.municipal_map`)
liga o resultado do SQL à malha pelo **código IBGE de 7 dígitos**. O SQL nem
sempre traz o código: muitas tabelas só têm o nome ("São Paulo", "SAO PAULO",
"Mogi das Cruzes - SP"). Aqui o nome vira código, dentro da UF quando ela é
conhecida — "Bom Jesus" existe em 5 estados, e sem a UF não há como escolher.

Dados em `journey/data/` (gerados por `scripts/fetch_ibge_municipios.py`):
`municipios.json` (código → nome, UF) e `municipios/<UF>.json` (código →
contornos lon/lat achatados).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rag.journey.text import fold

_DATA = Path(__file__).resolve().parent / "data"
# Acima disto o mapa municipal vira só ranking: com o Brasil inteiro (5.570
# polígonos) o desenho pesa e cada município vira um pixel.
MAX_UFS_MAPA = 6
# "Mogi das Cruzes - SP", "Mogi das Cruzes/SP", "Mogi das Cruzes (SP)"
_SUFIXO_UF = re.compile(r"^(.*?)\s*(?:[-/–(]\s*)([A-Za-z]{2})\)?\s*$")


@lru_cache(maxsize=1)
def _nomes() -> Dict[str, Tuple[str, str]]:
    with open(_DATA / "municipios.json", encoding="utf-8") as fh:
        return {cod: (nome, uf) for cod, (nome, uf) in json.load(fh).items()}


@lru_cache(maxsize=1)
def _indice() -> Tuple[Dict[Tuple[str, str], str], Dict[str, List[str]]]:
    por_uf: Dict[Tuple[str, str], str] = {}
    por_nome: Dict[str, List[str]] = {}
    for cod, (nome, uf) in _nomes().items():
        chave = fold(nome)
        por_uf[(uf, chave)] = cod
        por_nome.setdefault(chave, []).append(cod)
    return por_uf, por_nome


def name(code: str) -> str:
    return _nomes().get(str(code), ("", ""))[0]


def uf_of(code: str) -> str:
    return _nomes().get(str(code), ("", ""))[1]


def resolve(label, uf: Optional[str] = None) -> Optional[str]:
    """Código IBGE (7 dígitos) de um rótulo de município, ou None.

    Aceita o código (7 dígitos, ou 6 sem o dígito verificador, como em bases
    do Ministério da Saúde), o nome com ou sem acento e o nome com a UF colada.
    Sem UF, só resolve nome que existe em UM estado."""
    if label is None:
        return None
    texto = str(label).strip()
    if texto.endswith(".0"):
        texto = texto[:-2]
    if texto.isdigit():
        if len(texto) == 7 and texto in _nomes():
            return texto
        if len(texto) == 6:
            achado = [c for c in _nomes() if c.startswith(texto)]
            return achado[0] if len(achado) == 1 else None
        return None
    por_uf, por_nome = _indice()

    def busca(nome: str, sigla: Optional[str]) -> Optional[str]:
        chave = fold(nome)
        if sigla:
            return por_uf.get((sigla.upper(), chave))
        candidatos = por_nome.get(chave, [])
        return candidatos[0] if len(candidatos) == 1 else None

    achado = busca(texto, uf)
    if achado:
        return achado
    # Só depois do nome inteiro: "Embu-Guaçu" é nome, não "Embu" + UF.
    colada = _SUFIXO_UF.match(texto)
    if colada:
        return busca(colada.group(1), colada.group(2))
    return None


@lru_cache(maxsize=27)
def geometry(uf: str) -> Dict[str, List[List[Tuple[float, float]]]]:
    """Contornos (lon, lat) de cada município de uma UF; {} se não houver malha."""
    arquivo = _DATA / "municipios" / f"{uf.upper()}.json"
    if not arquivo.exists():
        return {}
    with open(arquivo, encoding="utf-8") as fh:
        bruto = json.load(fh)
    return {cod: [list(zip(anel[0::2], anel[1::2])) for anel in aneis]
            for cod, aneis in bruto.items()}
