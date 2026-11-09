"""Número ou assunto? — o primeiro desvio da jornada (ADR-0020), módulo puro.

O diagrama da jornada tem uma decisão logo depois da pergunta: quem pergunta
**um número** ("quanto foi contratado por UF?") vai para o motor que gera e
executa SQL; quem pergunta **um assunto** ("o que vocês têm sobre habitação?")
vai para o panorama do catálogo (modo Explorar).

Por que regra e não classificador de LLM: o ADR-0014 recusou o roteamento
automático porque erro de roteamento é invisível e não mensurável. Aqui o erro
fica visível e barato: a resposta de assunto oferece as perguntas de número como
botões, e a de número tem o botão "Como esse dado foi calculado?". O default é
**número**, porque é a forma que a População geral entende ("quanto?") e a que o
motor medido da Persona 2 responde.
"""
from __future__ import annotations

import re

from rag.journey.text import fold

NUMERO = "numero"
ASSUNTO = "assunto"

# Pedido explícito de panorama/catálogo. Ancorado em frases, não em palavras
# soltas: "dados" sozinho aparece em qualquer pergunta.
_ASSUNTO = [
    re.compile(p) for p in (
        r"\bo que (voces|vcs|vc|voce|o sistema|a base) (tem|tens|possui|possuem)\b",
        r"\bo que (existe|ha|tem) (sobre|de|a respeito)\b",
        r"\bque (dados|bases|tabelas|informacoes) (voces|vcs|o sistema|existem|ha|tem)\b",
        r"\bquais (sao )?(as )?(bases|tabelas|fontes|dados|informacoes)\b",
        r"\b(me )?(fale|explique|conte|mostre) (um pouco )?sobre\b",
        r"\bpanorama\b",
        r"\bcatalogo\b",
        r"\bo que (da|posso|consigo) (para )?(medir|perguntar|consultar|saber)\b",
        r"\bpor onde (comeco|comecar)\b",
    )
]

# Marcas de pergunta quantitativa. Se aparecerem, a pergunta é de número mesmo
# que também cite "dados" ou "base".
_NUMERO = [
    re.compile(p) for p in (
        r"\bquant[oa]s?\b",
        r"\b(total|soma|somatorio|media|mediana|percentual|porcentagem|taxa|indice)\b",
        r"\b(numero|qtd|quantidade|volume|valor|montante) de\b",
        r"\b(ranking|maior(es)?|menor(es)?|top \d+)\b",
        r"\b(evolu\w*|cresc\w*|caiu|subiu|variacao|serie)\b",
        r"\bpor (uf|estado|estados|municipio|municipios|regiao|ano|mes|trimestre)\b",
        r"\bem (19|20)\d\d\b",
    )
]


def classify(question: str) -> str:
    """`numero` (default) ou `assunto`."""
    texto = fold(question)
    if not texto:
        return NUMERO
    if any(p.search(texto) for p in _NUMERO):
        return NUMERO
    if any(p.search(texto) for p in _ASSUNTO):
        return ASSUNTO
    return NUMERO
