"""UFs, regiões e população (IBGE, Censo 2022) — módulo puro.

Dado oficial, versionado com o código em `data/ibge_uf.json` (a fonte e a data
do download estão no próprio arquivo). Serve três coisas da jornada: reconhecer
que a dimensão do resultado é UF (sigla, nome ou código), somar por região e
calcular "por habitante".
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from rag.journey.text import fold

_DATA = Path(__file__).resolve().parent / "data"
REGIOES = ("Norte", "Nordeste", "Centro-Oeste", "Sudeste", "Sul")


@lru_cache(maxsize=1)
def _raw() -> dict:
    with open(_DATA / "ibge_uf.json", encoding="utf-8") as fh:
        return json.load(fh)


def ufs() -> List[dict]:
    return list(_raw()["ufs"])


def population_brazil() -> int:
    return int(_raw()["populacao_brasil_2022"])


def population_source() -> str:
    return _raw()["fonte"]["populacao"]


@lru_cache(maxsize=1)
def _index() -> Dict[str, dict]:
    idx: Dict[str, dict] = {}
    for uf in ufs():
        idx[uf["sigla"].lower()] = uf
        idx[fold(uf["nome"])] = uf
        idx[str(uf["codigo"])] = uf
    return idx


def resolve(value) -> Optional[dict]:
    """A UF que este valor nomeia (sigla, nome ou código IBGE), ou None."""
    if value is None:
        return None
    texto = str(value).strip()
    if texto.endswith(".0"):  # código que veio como float
        texto = texto[:-2]
    return _index().get(fold(texto)) or _index().get(texto.lower())


def by_sigla(sigla: str) -> Optional[dict]:
    return _index().get((sigla or "").lower())


def geojson_path() -> Path:
    return _DATA / "br_uf_malha_ibge.geojson"
