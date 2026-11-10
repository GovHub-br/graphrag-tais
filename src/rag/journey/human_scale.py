"""O número em escala humana (ADR-0020): "o que esse número significa?".

Regra do board da jornada: **a comparação vem de uma tabela fixa e o LLM nunca a
inventa**. Aqui o LLM nem participa — a escolha é por regra:

1. Dinheiro → **por habitante** (IBGE, Censo 2022). Com dimensão UF, cada UF é
   dividida pela própria população; sem ela, divide-se pelo Brasil.
2. Contagem → a primeira referência de `config/escala_humana.yml` cuja razão
   caia na faixa legível (`min`/`max`). Não cabendo nenhuma, "1 a cada N
   habitantes" do Brasil.
3. Métrica não aditiva (taxa, média) → não há escala humana honesta. Nada.

Toda frase sai com a fonte junto, para a trilha técnica poder mostrar de onde
veio a comparação.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from loguru import logger

from rag.journey import fmt, ibge
from rag.journey.facts import Facts


@dataclass
class Scale:
    text: str      # frase pronta, ex.: "Dá R$ 48,20 por habitante do Brasil."
    source: str    # de onde veio a referência


@dataclass
class Reference:
    id: str
    singular: str
    plural: str
    valor: float
    aplica_a: str
    min: float
    max: float
    fonte: str


@lru_cache(maxsize=4)
def _load_references(path: str) -> tuple:
    arquivo = Path(path)
    if not arquivo.exists():
        return ()
    try:
        import yaml

        dados = yaml.safe_load(arquivo.read_text(encoding="utf-8")) or {}
        return tuple(
            Reference(
                id=str(r["id"]), singular=str(r["singular"]), plural=str(r["plural"]),
                valor=float(r["valor"]), aplica_a=str(r.get("aplica_a", "contagem")),
                min=float(r.get("min", 1)), max=float(r.get("max", 1e9)),
                fonte=str(r.get("fonte", "")),
            )
            for r in dados.get("referencias", [])
        )
    except Exception as exc:  # noqa: BLE001 — sem tabela, sem comparação
        logger.warning("[jornada/escala] {} ilegível ({}); sem referências fixas", path, exc)
        return ()


def references(path: str | None = None) -> List[Reference]:
    if path is None:
        from rag.settings.configs import settings

        path = settings.JOURNEY_HUMAN_SCALE_PATH
    return list(_load_references(path))


def scale_for(facts: Facts, path: str | None = None) -> Optional[Scale]:
    if not facts.ok or not facts.additive or facts.headline_kind == "maior":
        return None
    valor = facts.headline
    if valor is None or valor <= 0:
        return None
    fonte_pop = "IBGE, Censo 2022"

    if facts.money:
        # Série: o último período sobre o Brasil; total: idem. Com UF, a média
        # nacional é a soma das UFs sobre a soma das populações delas — só bate
        # com o Brasil quando as 27 estão no resultado.
        populacao = ibge.population_brazil()
        onde = "do Brasil"
        if facts.uf_values and facts.headline_kind == "total" and len(facts.uf_values) < 27:
            populacao = sum(ibge.by_sigla(s)["populacao_2022"] for s in facts.uf_values)
            onde = f"das {len(facts.uf_values)} UFs do resultado"
        por_hab = valor / populacao
        return Scale(text=f"Dá {fmt.exact(por_hab, money=True)} por habitante {onde}.",
                     source=fonte_pop)

    for ref in references(path):
        if ref.aplica_a != "contagem":
            continue
        razao = valor / ref.valor
        if ref.min <= razao <= ref.max:
            # Comparação é ordem de grandeza: "40 Maracanãs", não "39,6".
            quantos = (fmt.exact(round(razao)) if razao >= 10
                       else fmt.full(razao, decimals=1).replace(",0", ""))
            rotulo = ref.singular if razao < 1.95 else ref.plural
            return Scale(text=f"É como {quantos} {rotulo}.", source=ref.fonte)

    a_cada = ibge.population_brazil() / valor
    if 1 <= a_cada <= 1e7:
        return Scale(text=f"É 1 para cada {fmt.exact(round(a_cada))} habitantes do Brasil.",
                     source=fonte_pop)
    return None
