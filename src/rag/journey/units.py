"""Unidade da métrica, lida do catálogo (módulo puro).

O banco guarda o número; a **descrição da coluna** no catálogo diz o que ele é
("Saldo total das cadernetas ao fim do mês, em milhões de reais"). Sem ler
isso, `761951` vira "762 mil" na tela quando o saldo é R$ 762 bilhões, e um
número-índice vira "1,3 mil".

Nada aqui conhece base, órgão ou tabela: são padrões de português (e de inglês,
para catálogos que vierem assim) que qualquer catálogo documentado usa. A
fonte é a descrição que o motor já devolve em `answer["references"]` — a mesma
que vem do OpenMetadata na ingestão —, então plugar outra camada semântica
basta para funcionar. Sem descrição, o nome da coluna é o último recurso.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Iterable

#: Tipos de unidade que mudam a FORMA de mostrar o número.
DINHEIRO = "dinheiro"
INDICE = "indice"

_ESCALA = {
    "mil": 1e3, "milhar": 1e3, "milhares": 1e3, "thousand": 1e3, "thousands": 1e3,
    "milhao": 1e6, "milhoes": 1e6, "million": 1e6, "millions": 1e6,
    "bilhao": 1e9, "bilhoes": 1e9, "billion": 1e9, "billions": 1e9,
    "trilhao": 1e12, "trilhoes": 1e12,
}
_NOMES_ESCALA = {1e3: "milhares", 1e6: "milhões", 1e9: "bilhões", 1e12: "trilhões"}
# "em milhões de reais", "R$ mil", "valores em milhares", "in millions".
# Exige "em"/"in"/"R$" antes: "mil empreendimentos" numa descrição é contagem,
# não unidade.
_ESCALA_RE = re.compile(r"(?:\bem|\bin|r\$)\s+(?:r\$\s+)?(" + "|".join(_ESCALA) + r")\b")
_DINHEIRO_RE = re.compile(r"(\breais\b|r\$|\bbrl\b|\bmoeda\b|\bmonetari)")
# Número-índice: nível de um índice (INCC, IPCA, base 100), não uma contagem.
_INDICE_RE = re.compile(r"(\bnumero[ -]indice\b|\bindice\b|\bindex\b|\bbase 100\b|\bpontos\b)")
# A descrição de uma VARIAÇÃO cita o índice ("variação do índice no mês") —
# isso é taxa, não o nível do índice.
_VARIACAO_RE = re.compile(r"\b(variacao|var|percentual|taxa|razao|proporcao)\b")
_NOME_INDICE = re.compile(r"(^|_)(indice|index|idx|numero_indice)($|_)")
_IDENT = re.compile(r"[a-z_][a-z0-9_]*")


@dataclass(frozen=True)
class Unit:
    kind: str = ""        # "" | dinheiro | indice
    #: Multiplicador para chegar à unidade base (1e6 para "em milhões").
    factor: float = 1.0
    #: De onde veio a leitura — vai para a nota da trilha técnica.
    source: str = ""

    @property
    def scaled(self) -> bool:
        return self.factor != 1.0

    def note(self) -> str:
        """Frase para quem quer saber por que o número da tela difere do banco."""
        if not self.scaled:
            return ""
        escala = _NOMES_ESCALA.get(self.factor, f"× {self.factor:g}")
        coisa = " de reais" if self.kind == DINHEIRO else ""
        return (f"Na base, os valores estão em {escala}{coisa} (descrição da coluna no "
                "catálogo); a resposta já mostra o valor por extenso.")


def _norm(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sem_acento.lower())


def from_description(description: str) -> Unit:
    """Unidade dita na descrição de UMA coluna."""
    texto = _norm(description)
    if not texto:
        return Unit()
    achado = _ESCALA_RE.search(texto)
    fator = _ESCALA[achado.group(1)] if achado else 1.0
    if _DINHEIRO_RE.search(texto):
        return Unit(DINHEIRO, fator, "catalogo")
    if _INDICE_RE.search(texto) and not _VARIACAO_RE.search(texto):
        return Unit(INDICE, 1.0, "catalogo")
    return Unit("", fator, "catalogo" if achado else "")


def source_columns(metric: str, source_expr: str) -> list:
    """Colunas físicas de onde a métrica sai: a do `SUM(x) AS metric` e o
    próprio nome (quando o SELECT não tem alias)."""
    nomes = [n for n in _IDENT.findall((source_expr or "").lower())
             if n not in {"sum", "avg", "max", "min", "count", "coalesce", "cast", "as",
                          "numeric", "round", "distinct", "float", "double", "precision",
                          "nullif", "abs"}]
    return list(dict.fromkeys(nomes + [(metric or "").lower()]))


def resolve(metric: str, source_expr: str,
            references: Iterable[Dict[str, Any]]) -> Unit:
    """Unidade da métrica a partir das referências do motor.

    `COUNT(...)` é contagem, e contagem não herda unidade de coluna nenhuma.
    Procura a descrição nas tabelas USADAS primeiro; sem descrição útil, cai no
    nome da coluna (só para índice — "valor" no nome já é tratado em `facts`).
    """
    if re.search(r"\bcount\s*\(", (source_expr or "").lower()):
        return Unit()
    colunas = source_columns(metric, source_expr)
    refs = sorted(list(references or []), key=lambda r: not r.get("used"))
    for nome in colunas:
        for ref in refs:
            for col in ref.get("columns") or []:
                if str(col.get("name", "")).lower() == nome:
                    unidade = from_description(col.get("description") or "")
                    if unidade.kind or unidade.scaled:
                        return unidade
    if any(_NOME_INDICE.search(n) for n in colunas):
        return Unit(INDICE, 1.0, "nome")
    return Unit()
