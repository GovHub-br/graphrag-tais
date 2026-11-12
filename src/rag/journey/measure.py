"""A métrica é o que o apelido dela diz? (módulo puro)

O motor escreve `SUM(coluna) AS apelido`, e o apelido é o que a resposta
mostra como nome do número. Quando nenhuma coluna das tabelas recuperadas mede
o que foi perguntado, o LLM às vezes pega OUTRA coluna e lhe dá o nome da
pergunta:

    SELECT uf, SUM(renda_familiar) AS total_contratado …

A execução funciona, o número sai bonito, e a resposta afirma "o valor
contratado no FAR foi 899 milhões", que é a soma da renda das famílias
(caso real, MCid, 10/11/2026). Nenhuma guarda anterior pegava: a do número
confere a frase contra o cartão, não o cartão contra a pergunta.

A regra olha só a FORMA: as palavras de conteúdo do apelido ("contratado")
têm de aparecer, pelo radical, no nome ou na descrição da coluna somada
("renda_familiar"). Nada de base, órgão ou tabela.

É conservadora de propósito. Só julga `AGREGAÇÃO(coluna) AS apelido` com uma
coluna só; `COUNT` não julga (o apelido nomeia a coisa contada, não a coluna);
coluna de nome abreviado demais para comparar (`qt_uh`) e apelido genérico
(`total`, `valor`) também não. Na dúvida, não acusa: um falso positivo esconde
uma resposta certa.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

#: Palavras que não dizem O QUE é medido: agregação, unidade, preposição.
_GENERICO = {
    "total", "totais", "soma", "somatorio", "sum", "qt", "qtd", "qtde", "quantidade",
    "valor", "valores", "vl", "vlr", "vr", "media", "medio", "avg", "mean", "num",
    "numero", "nr", "count", "contagem", "montante", "geral", "acumulado", "agregado",
    "resultado", "indicador", "metrica", "value", "amount", "tot", "max", "min",
    "maximo", "minimo", "de", "do", "da", "dos", "das", "por", "em", "no", "na",
    "the", "of", "per", "reais", "rs", "brl",
}
_MIN = 4          # palavra mais curta que conta como conteúdo
_RADICAL = 5      # letras do começo que bastam para "é a mesma palavra"
_AGREGADO = re.compile(
    r"\b(sum|avg|max|min)\s*\(\s*(?:distinct\s+)?(?:\w+\.)?\"?(\w+)\"?\s*\)\s+"
    r"(?:as\s+)?\"?(\w+)\"?\s*(?=,|\bfrom\b|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Misnamed:
    """A coluna que de fato foi somada e o nome que o resultado recebeu."""
    column: str           # renda_familiar
    alias: str            # total_contratado
    missing: List[str]    # ["contratado"]: o que o apelido promete e a coluna não tem


def _fold(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in sem_acento if not unicodedata.combining(c)).lower()


def _words(texto: str) -> List[str]:
    return [p for p in re.split(r"[^a-z0-9]+", _fold(texto))
            if len(p) >= _MIN and p not in _GENERICO and not p.isdigit()]


def _same(a: str, b: str) -> bool:
    if a == b:
        return True
    n = min(len(a), len(b), _RADICAL)
    return n >= _RADICAL and a[:n] == b[:n]


def _description(column: str, references: Iterable[Dict[str, Any]]) -> str:
    textos = []
    for ref in references or []:
        for col in ref.get("columns") or []:
            if str(col.get("name", "")).lower() == column:
                textos.append(str(col.get("description") or ""))
    return " ".join(textos)


def misnamed(metric: str, sql: str,
             references: Iterable[Dict[str, Any]] = ()) -> Optional[Misnamed]:
    """`Misnamed` quando o apelido da métrica promete o que a coluna somada
    não é; None quando bate ou quando não dá para julgar."""
    if not metric or not sql:
        return None
    for achado in _AGREGADO.finditer(sql):
        _, coluna, apelido = achado.groups()
        if apelido.lower() != metric.lower():
            continue
        coluna = coluna.lower()
        if coluna == apelido.lower():
            return None
        promete = _words(apelido)
        # O nome da coluna e, se o catálogo tiver, a descrição dela: `vl_ted`
        # sozinho não diz nada, "Valor repassado por TED" diz.
        tem = _words(coluna) + _words(_description(coluna, references))
        if not promete or not tem:
            return None   # apelido genérico ou coluna muda: não dá para julgar
        faltam = [p for p in promete if not any(_same(p, t) for t in tem)]
        # Só acusa quando NADA do que o apelido promete está na coluna:
        # "unidades_habitacionais_entregues" sobre `qt_uh_entregues` tem
        # "entregues" em comum e passa.
        if len(faltam) == len(promete):
            return Misnamed(column=coluna, alias=apelido, missing=faltam)
        return None
    return None
