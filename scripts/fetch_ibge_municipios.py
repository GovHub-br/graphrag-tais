"""Baixa do IBGE a malha e os nomes dos municípios para o mapa municipal da jornada.

Gera, em `src/rag/journey/data/`:

- `municipios.json`            {código IBGE (7 dígitos): [nome, sigla da UF]};
- `municipios/<UF>.json`       {código: [[x0, y0, x1, y1, …], …]} — contorno
                               externo de cada polígono, lon/lat com 3 casas
                               (~100 m: sobra para um mapa de tela).

Fonte: API de malhas v3 do IBGE, qualidade "mínima", um estado por vez (o mapa
só desenha o estado clicado), e a API de localidades v1. Rode de novo quando o
IBGE mudar a malha (município novo é raro: o último foi em 2013).

    python scripts/fetch_ibge_municipios.py
"""
from __future__ import annotations

import gzip
import json
import time
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "src" / "rag" / "journey" / "data"
MALHA = ("https://servicodados.ibge.gov.br/api/v3/malhas/estados/{codigo}"
         "?intrarregiao=municipio&formato=application/vnd.geo%2Bjson&qualidade=minima")
NOMES = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"


def _get(url: str):
    for tentativa in range(4):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                corpo = r.read()
            # A API de localidades devolve gzip mesmo sem Accept-Encoding.
            if corpo[:2] == b"\x1f\x8b":
                corpo = gzip.decompress(corpo)
            return json.loads(corpo)
        except Exception:  # noqa: BLE001 — a API do IBGE às vezes cai; tenta de novo
            if tentativa == 3:
                raise
            time.sleep(3 * (tentativa + 1))


def _anel(pontos) -> list:
    """Contorno com 3 casas e sem ponto repetido em sequência, achatado."""
    plano, ultimo = [], None
    for x, y in pontos:
        p = (round(x, 3), round(y, 3))
        if p != ultimo:
            plano.extend(p)
            ultimo = p
    return plano


def main() -> None:
    ufs = json.loads((DATA / "ibge_uf.json").read_text(encoding="utf-8"))["ufs"]
    destino = DATA / "municipios"
    destino.mkdir(exist_ok=True)
    total = 0
    for uf in ufs:
        malha = _get(MALHA.format(codigo=uf["codigo"]))
        saida = {}
        for feature in malha["features"]:
            geo = feature["geometry"]
            polys = geo["coordinates"] if geo["type"] == "MultiPolygon" else [geo["coordinates"]]
            aneis = [a for a in (_anel(p[0]) for p in polys) if len(a) >= 6]
            if aneis:
                saida[str(feature["properties"]["codarea"])] = aneis
        texto = json.dumps(saida, separators=(",", ":"))
        (destino / f"{uf['sigla']}.json").write_text(texto, encoding="utf-8")
        total += len(texto)
        print(f"{uf['sigla']}: {len(saida)} municípios, {len(texto) // 1024} KB")
    nomes = {str(m["id"]): [m["nome"], m["microrregiao"]["mesorregiao"]["UF"]["sigla"]]
             if m.get("microrregiao") else [m["nome"], m["regiao-imediata"]["regiao-intermediaria"]["UF"]["sigla"]]
             for m in _get(NOMES)}
    (DATA / "municipios.json").write_text(
        json.dumps(nomes, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"malhas: {total // 1024} KB; nomes: {len(nomes)} municípios")


if __name__ == "__main__":
    main()
