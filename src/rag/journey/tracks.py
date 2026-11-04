"""Catálogo das trilhas da jornada (ADR-0020) — módulo puro.

A persona não aparece para quem pergunta: ela vira um **botão**. O botão é o
texto exato que o OpenWebUI mostra como sugestão de continuação e que volta como
mensagem do usuário quando clicado — por isso o rótulo é contrato. Mudar o texto
de um botão é mudar a forma como o clique é reconhecido (`match_track`), e
conversas antigas com o rótulo velho deixam de abrir a trilha.

A População geral não tem botão: ela é a resposta padrão, a primeira que a
pessoa recebe.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from rag.journey.text import fold

#: Trilha implícita da primeira resposta. Não tem botão.
POPULACAO = "populacao"


@dataclass(frozen=True)
class Track:
    id: str
    persona: str
    button: str
    #: Outras formas de pedir a mesma trilha, digitadas à mão.
    aliases: tuple = ()
    #: Termos da PERGUNTA que sugerem esta trilha primeiro (palpite de ordem).
    hint_terms: tuple = ()
    #: True para as três personas específicas; False para história e baixar,
    #: que valem para todo mundo e por isso vêm depois delas.
    persona_track: bool = True


TECNICA = Track(
    id="tecnica",
    persona="Técnica",
    button="Como esse dado foi calculado?",
    aliases=("como foi calculado", "como calculou", "mostre o sql", "ver o sql",
             "detalhes tecnicos"),
    hint_terms=("sql", "tabela", "coluna", "consulta", "query", "metodologia",
                "calcul", "fonte", "dicionario", "csv", "base de dados"),
)
POLITICA = Track(
    id="politica",
    persona="Especialista em políticas",
    button="Contexto da política",
    aliases=("contexto da politica publica", "qual a politica por tras",
             "contexto"),
    hint_terms=("politica", "programa", "lei", "historic", "evolu", "regiao",
                "regioes", "desigual", "raca", "genero", "impacto"),
)
GESTOR = Track(
    id="gestor",
    persona="Gestor público",
    button="Números para decisão",
    aliases=("numeros para decidir", "indicadores para decisao",
             "painel do gestor"),
    hint_terms=("meta", "executad", "previst", "ranking", "melhor", "pior",
                "decis", "gest", "desempenho", "prioridade", "comparar"),
)
HISTORIA = Track(
    id="historia",
    persona="Todos",
    button="Contar a história",
    aliases=("conte a historia", "contar a historia do dado", "historia"),
    persona_track=False,
)
BAIXAR = Track(
    id="baixar",
    persona="Todos",
    button="Baixar dados e relatório",
    aliases=("baixar os dados", "baixar dados", "exportar", "download",
             "baixar relatorio"),
    persona_track=False,
)

PERSONA_TRACKS: tuple = (TECNICA, POLITICA, GESTOR)
ALL_TRACKS: tuple = PERSONA_TRACKS + (HISTORIA, BAIXAR)
_BY_ID = {t.id: t for t in ALL_TRACKS}


def get(track_id: str) -> Optional[Track]:
    return _BY_ID.get(track_id)


def match_track(text: str) -> Optional[Track]:
    """A trilha que esta mensagem pede, ou None se for uma pergunta nova.

    Casamento por IGUALDADE do texto normalizado, nunca por "contém": "como esse
    dado foi calculado para SP em 2023?" é uma pergunta nova que merece motor
    próprio, não o clique do botão.
    """
    alvo = fold(text)
    if not alvo:
        return None
    for track in ALL_TRACKS:
        if alvo == fold(track.button) or alvo in {fold(a) for a in track.aliases}:
            return track
    return None


def suggest_order(question: str, done: Iterable[str] = ()) -> List[Track]:
    """Botões na ordem do palpite. Todos continuam visíveis (ADR-0020).

    O sistema só **sugere**: ordena as três personas pela afinidade da pergunta
    com cada uma (contagem de termos) e mantém a ordem do catálogo no empate.
    História e baixar vêm sempre depois, porque valem para todos. Trilha já
    vista sai da lista — o botão dela já foi usado nesta conversa.
    """
    vistos = set(done)
    texto = fold(question)

    def score(track: Track) -> int:
        return sum(1 for termo in track.hint_terms if termo in texto)

    personas = [t for t in PERSONA_TRACKS if t.id not in vistos]
    ordem = {t.id: i for i, t in enumerate(PERSONA_TRACKS)}
    personas.sort(key=lambda t: (-score(t), ordem[t.id]))
    extras = [t for t in (HISTORIA, BAIXAR) if t.id not in vistos]
    return personas + extras


def buttons(tracks: Sequence[Track]) -> List[str]:
    return [t.button for t in tracks]
