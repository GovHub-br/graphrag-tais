"""Contexto da conversa: a pergunta de seguimento vira uma pergunta completa.

O OpenWebUI manda o histórico inteiro a cada mensagem, mas as três pipelines
(motor, Explorar, Jornada) recebem só a ÚLTIMA pergunta: "e em São Paulo?"
chegava ao retrieval sem dizer de quê, e a resposta saía sobre qualquer coisa.
Aqui, antes da pipeline, a pergunta é reescrita com o histórico ("Quanto foi
contratado no FAR em São Paulo?"). A pipeline continua recebendo UMA pergunta
autossuficiente, como sempre recebeu — o caminho medido não muda, e sem
histórico nada acontece (nenhuma chamada de LLM a mais).

O histórico vai resumido: as perguntas da pessoa e a primeira frase de cada
resposta, sem imagem, tabela, código nem marcador. Número que só aparece numa
resposta não entra na reescrita, então não vira filtro inventado.

Módulo puro nas regras (`previous_turns`, `parse_rewrite`); a chamada ao LLM é
injetada (`rewrite`), para teste sem rede.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

Turn = Tuple[str, str]                       # (pergunta, começo da resposta)
RewriteFn = Callable[[str, str], str]       # (histórico, pergunta) → JSON do LLM

_IMAGEM = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_CODIGO = re.compile(r"```.*?```", re.S)
_DETALHES = re.compile(r"<details>.*?</details>", re.S)
_TAG = re.compile(r"<[^>]+>")
_FIM_DE_FRASE = re.compile(r"(?<=[.!?])\s+")
_MAX_RESPOSTA = 280
_MAX_PERGUNTA = 400


def content_of(message: Dict) -> str:
    """Texto de uma mensagem do OpenAI-compat (string ou lista de partes)."""
    conteudo = message.get("content", "")
    if isinstance(conteudo, list):
        return " ".join(p["text"] for p in conteudo if isinstance(p, dict) and p.get("text"))
    return str(conteudo or "")


def answer_gist(markdown: str, limit: int = _MAX_RESPOSTA) -> str:
    """As primeiras frases de uma resposta, sem o que só faz sentido na tela."""
    texto = _CODIGO.sub(" ", markdown or "")
    texto = _DETALHES.sub(" ", texto)
    linhas = []
    for linha in texto.splitlines():
        crua = linha.strip()
        if not crua or crua.startswith(("|", "![", "[//]:", "#", "<")):
            continue
        # Linha toda em itálico é nota de rodapé (fonte, aviso), não resposta.
        if crua.startswith("*") and not crua.startswith("**") and crua.endswith("*"):
            continue
        linhas.append(crua)
    texto = _IMAGEM.sub(" ", " ".join(linhas))
    texto = _LINK.sub(r"\1", texto)
    texto = _TAG.sub(" ", texto).replace("*", "").replace("`", "")
    texto = re.sub(r"\s+", " ", texto).strip()
    if len(texto) <= limit:
        return texto
    frases, saida = _FIM_DE_FRASE.split(texto), ""
    for frase in frases:
        if len(saida) + len(frase) > limit:
            break
        saida = f"{saida} {frase}".strip()
    return saida or texto[:limit].rstrip() + "…"


def previous_turns(messages: Sequence[Dict], *, skip: Optional[Callable[[str], bool]] = None,
                   limit: int = 3) -> List[Turn]:
    """Os últimos `limit` turnos ANTES da mensagem atual.

    `skip` diz quais mensagens do usuário não são pergunta (clique em botão,
    pedido de bastidor do OpenWebUI): elas e as respostas delas ficam de fora,
    mas a pergunta a que o botão se referia continua no histórico."""
    msgs = list(messages or [])
    # A mensagem atual é a última do usuário; tudo antes dela é histórico.
    for fim in range(len(msgs) - 1, -1, -1):
        if msgs[fim].get("role") == "user":
            msgs = msgs[:fim]
            break
    else:
        return []
    turnos: List[Turn] = []
    pergunta: Optional[str] = None
    for msg in msgs:
        papel, texto = msg.get("role"), content_of(msg).strip()
        if papel == "user":
            pergunta = None if (not texto or (skip and skip(texto))) else texto
        elif papel == "assistant" and pergunta:
            turnos.append((pergunta, answer_gist(texto)))
            pergunta = None
    return turnos[-limit:]


def render_history(turns: Sequence[Turn]) -> str:
    return "\n".join(f"Pessoa: {p}\nAssistente: {r or '(resposta sem texto)'}" for p, r in turns)


def parse_rewrite(raw: str, original: str) -> str:
    """`{"pergunta": "..."}` do LLM; qualquer coisa estranha devolve a original."""
    texto = (raw or "").strip()
    try:
        dados = json.loads(texto[texto.find("{"): texto.rfind("}") + 1])
        nova = str(dados.get("pergunta", "")).strip()
    except (ValueError, AttributeError):
        return original
    if not nova or len(nova) > _MAX_PERGUNTA:
        return original
    return nova


def standalone_question(question: str, messages: Sequence[Dict], rewrite: RewriteFn, *,
                        skip: Optional[Callable[[str], bool]] = None, limit: int = 3) -> str:
    """A pergunta atual, completa com o contexto da conversa.

    Sem histórico, devolve a pergunta como veio, sem chamar o LLM. Falha do
    LLM também devolve a original: o contexto é melhoria, não dependência."""
    turnos = previous_turns(messages, skip=skip, limit=limit)
    if not turnos or not (question or "").strip():
        return question
    try:
        return parse_rewrite(rewrite(render_history(turnos), question), question)
    except Exception as exc:  # noqa: BLE001 — sem reescrita, segue a pergunta original
        from loguru import logger

        logger.warning("[conversa] reescrita falhou ({}); seguindo com a pergunta como veio", exc)
        return question


def same_question(a: str, b: str) -> bool:
    """Reescrita que só mexeu em pontuação ou caixa não conta como mudança."""
    def norm(t: str) -> str:
        return re.sub(r"[^\w]+", " ", (t or "").lower()).strip()

    return norm(a) == norm(b)


def interpreted_note(original: str, rewritten: str) -> str:
    """A linha que mostra como a pergunta foi entendida — a pessoa confere se
    o "e em SP?" pegou o assunto certo, e corrige se não pegou."""
    if same_question(original, rewritten):
        return ""
    return f"*Considerando a conversa, entendi a pergunta como: “{rewritten}”*"


def with_context(text: str, messages: Sequence[Dict], *,
                 skip: Optional[Callable[[str], bool]] = None, tag: str = "chat") -> str:
    """O ponto de entrada dos wrappers: liga/desliga por config, ignora pedido
    de bastidor do OpenWebUI (título, tags, follow-ups) e loga a reescrita."""
    from loguru import logger

    from rag.journey import openwebui_tasks
    from rag.settings.configs import settings

    if not settings.CHAT_CONTEXT_ENABLED or openwebui_tasks.detect(text):
        return text

    def ignorar(t: str) -> bool:
        return bool(openwebui_tasks.detect(t) or (skip and skip(t)))

    pergunta = standalone_question(text, messages, llm_rewrite, skip=ignorar,
                                   limit=settings.CHAT_CONTEXT_TURNS)
    if pergunta != text:
        logger.info("[{}/contexto] '{}' → '{}'", tag, text[:60], pergunta[:120])
    return pergunta


def llm_rewrite(history: str, question: str) -> str:
    """Chamada real: modelo leve, saída JSON, poucos tokens."""
    from haystack.dataclasses import ChatMessage

    from rag import prompts
    from rag.components.generators.openai_generator import OpenAIChatGeneratorFactory
    from rag.settings.configs import settings

    gen = OpenAIChatGeneratorFactory(model=settings.CHAT_CONTEXT_MODEL, json_mode=True,
                                     max_tokens=200).build()
    reply = gen.run(messages=[
        ChatMessage.from_system(prompts.load("conversa_reescrita_system")),
        ChatMessage.from_user(prompts.render("conversa_reescrita_user", history=history,
                                             question=question)),
    ])
    return reply["replies"][0].text
